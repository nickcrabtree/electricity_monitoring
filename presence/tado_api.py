#!/usr/bin/env python3
"""
Tado API client for geofencing presence data
"""

import json
import logging
import os
import time
import shutil
import subprocess
import textwrap
from typing import Dict, Optional, List
import argparse
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class TadoAPI:
    """Client for Tado API to get presence/geofencing data"""
    
    BASE_URL = "https://my.tado.com/api/v2"
    AUTH_URL = "https://auth.tado.com/oauth/token"
    
    # Try multiple authentication endpoints due to recent API changes
    AUTH_URLS = [
        "https://auth.tado.com/oauth/token",
        "https://my.tado.com/oauth/token", 
        "https://my.tado.com/api/v2/oauth/token"
    ]
    
    # Public client credentials for legacy web app password grant
    CLIENT_ID = "tado-web-app"
    CLIENT_SECRET = "wZaRN7rpjn3FoNyF5IFuxg9uMzYJcvOoQ8QWiIqS3hfk6gLhVlG57j5YNoZL2Rtc"

    # Device-code flow constants from official Tado docs
    DEVICE_CLIENT_ID = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"
    DEVICE_TOKEN_URL = "https://login.tado.com/oauth2/token"
    
    def __init__(self, username: str, password: str, state_file: Optional[str] = None):
        """Initialize Tado client.

        Supports two auth modes:
        - Legacy username/password OAuth (now often disabled by Tado)
        - Static access token via TADO_ACCESS_TOKEN environment variable
        """
        self.username = username
        self.password = password
        self.state_file = state_file or "presence/state.json"
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = 0
        self.home_id = None
        self.user_info = None
        # True if we were given a static access token via env and should not
        # attempt OAuth flows (which now frequently return 410/401).
        self.token_from_env = False
        
        # Load cached tokens if available
        self._load_state()

        # If we already have tokens from state, prefer those and ignore
        # environment overrides. Environment variables are only used for
        # initial seeding when no tokens have been stored yet.
        if not self.access_token and not self.refresh_token:
            env_access = os.getenv("TADO_ACCESS_TOKEN")
            env_refresh = os.getenv("TADO_REFRESH_TOKEN")

            if env_refresh:
                # Trust the refresh token from env as the source of truth and
                # ignore any access token. We will fetch a fresh access token
                # on first use via _refresh_access_token.
                self.refresh_token = env_refresh
                self.access_token = None
                self.token_expires_at = 0
                self.token_from_env = False

            elif env_access:
                # Only an access token and no refresh; treat as static until
                # the API returns 401.
                self.access_token = env_access
                logger.info("Using Tado access token from TADO_ACCESS_TOKEN")
                if not self.token_expires_at:
                    self.token_expires_at = time.time() + 365 * 24 * 3600
                self.token_from_env = True
    
    def _load_state(self):
        """Load cached tokens and state from file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    
                self.access_token = state.get('tado_access_token')
                self.refresh_token = state.get('tado_refresh_token')
                self.token_expires_at = state.get('tado_token_expires_at', 0)
                self.home_id = state.get('tado_home_id')
                self.user_info = state.get('tado_user_info')
                
        except Exception as e:
            logger.warning(f"Could not load Tado state: {e}")
    
    def _save_state(self):
        """Save tokens and state to file"""
        try:
            # Load existing state if any
            state = {}
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, 'r') as f:
                        state = json.load(f)
                except:
                    pass
            
            # Update Tado-specific fields
            state.update({
                'tado_access_token': self.access_token,
                'tado_refresh_token': self.refresh_token,
                'tado_token_expires_at': self.token_expires_at,
                'tado_home_id': self.home_id,
                'tado_user_info': self.user_info
            })
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            # Write state file
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Could not save Tado state: {e}")
    
    def _is_token_valid(self) -> bool:
        """Check if current token is valid and not expired"""
        if not self.access_token:
            return False

        # If we were given a static access token via environment, assume it is
        # valid until the API tells us otherwise. We do not attempt any OAuth
        # refresh in this mode because password grants are deprecated.
        if self.token_from_env:
            return True
        
        # Add 5-minute buffer before expiry for OAuth tokens
        return time.time() < (self.token_expires_at - 300)
    
    def _authenticate(self) -> bool:
        """Authenticate with Tado API using password grant - try multiple endpoints"""
        
        data = {
            'grant_type': 'password',
            'username': self.username,
            'password': self.password,
            'scope': 'home.user'
        }
        
        auth = HTTPBasicAuth(self.CLIENT_ID, self.CLIENT_SECRET)
        
        # Try multiple auth URLs due to recent API changes
        for auth_url in self.AUTH_URLS:
            try:
                logger.debug(f"Trying authentication with {auth_url}")
                response = requests.post(auth_url, data=data, auth=auth, timeout=10)
                
                if response.status_code == 410:  # Gone - endpoint deprecated
                    logger.debug(f"Auth endpoint {auth_url} is deprecated (410)")
                    continue
                    
                response.raise_for_status()
                token_data = response.json()
                
                self.access_token = token_data['access_token']
                self.refresh_token = token_data.get('refresh_token')
                expires_in = token_data.get('expires_in', 3600)  # Default 1 hour
                self.token_expires_at = time.time() + expires_in
                
                logger.info(f"Successfully authenticated with Tado using {auth_url}")
                self._save_state()
                return True
                
            except requests.exceptions.RequestException as e:
                logger.debug(f"Auth failed for {auth_url}: {e}")
                continue
            except Exception as e:
                logger.debug(f"Auth error for {auth_url}: {e}")
                continue
        
        logger.error("All Tado authentication endpoints failed")
        return False
    
    def _refresh_access_token(self) -> bool:
        """Refresh access token using refresh token.

        Uses the new device-code flow token endpoint on login.tado.com as
        described in Tado's official documentation.
        """
        if not self.refresh_token:
            return False
        
        try:
            logger.debug("Refreshing Tado access token via device-flow endpoint")
            
            data = {
                'client_id': self.DEVICE_CLIENT_ID,
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
            }
            
            response = requests.post(self.DEVICE_TOKEN_URL, params=data, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            # Tado rotates refresh tokens; update if a new one is supplied
            rotated_refresh = token_data.get('refresh_token', self.refresh_token)
            self.refresh_token = rotated_refresh
            
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = time.time() + expires_in
            
            logger.debug("Successfully refreshed Tado token")
            # Persist updated tokens both to state and env so future restarts
            # use the latest refresh token rather than one that Tado has
            # already seen.
            try:
                self._save_state()
            finally:
                # Best-effort env update; failures just log a warning.
                try:
                    _update_env_with_tokens(self.access_token, self.refresh_token)
                except Exception as env_err:
                    logger.warning(f"Could not update env file with rotated Tado tokens: {env_err}")
            return True

        except requests.exceptions.HTTPError as e:
            # Inspect HTTP error to see if this looks like a permanent
            # refresh-token expiry (e.g. after max lifetime), and notify.
            resp = getattr(e, 'response', None)
            is_expired = False
            details = ""
            if resp is not None:
                try:
                    err_data = resp.json()
                    details = json.dumps(err_data)
                    error_code = err_data.get('error')
                    if error_code in ("expired_token", "invalid_grant"):
                        is_expired = True
                except Exception:
                    # Non-JSON body; fall back to raw text
                    try:
                        details = resp.text or ""
                    except Exception:
                        details = ""
            msg = f"Token refresh failed: {e}"
            if is_expired:
                logger.warning("Tado refresh token appears to have expired permanently; manual re-auth required")
                logger.warning(msg)
                self._notify_refresh_token_expired(details or msg)
            else:
                logger.warning(msg)
            return False
        except Exception as e:
            logger.warning(f"Token refresh failed: {e}")
            return False
    
    def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid access token"""
        # Static access-token mode: just use the token as-is.
        if self.token_from_env:
            if not self.access_token:
                logger.error("Tado access token not set but token_from_env=True")
                return False
            return True

        # OAuth mode: use cached/refreshable tokens.
        if self._is_token_valid():
            return True
        
        # Try to refresh first
        if self._refresh_access_token():
            return True
        
        # Fall back to full authentication
        return self._authenticate()
    
    def _api_request(self, endpoint: str) -> Optional[Dict]:
        """Make authenticated API request"""
        if not self._ensure_authenticated():
            logger.error("Could not authenticate with Tado API")
            return None
        
        url = f"{self.BASE_URL}{endpoint}"
        headers = {'Authorization': f'Bearer {self.access_token}'}

        try:
            response = requests.get(url, headers=headers, timeout=10)

            # If we get 401 and have a refresh token, try one automatic
            # refresh and retry the request once. This mirrors how Home
            # Assistant (via PyTado) relies on the refresh token as the
            # primary credential.
            if response.status_code == 401 and self.refresh_token:
                logger.warning(f"Tado API returned 401 for {endpoint}, attempting token refresh")
                if self._refresh_access_token():
                    headers['Authorization'] = f'Bearer {self.access_token}'
                    response = requests.get(url, headers=headers, timeout=10)

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Tado API request failed for {endpoint}: {e}")
            return None
    
    def get_user_info(self) -> Optional[Dict]:
        """Get user information and discover home ID"""
        if self.user_info:
            return self.user_info
        
        user_data = self._api_request("/me")
        if not user_data:
            return None
        
        self.user_info = user_data
        
        # Extract home ID from user data
        if 'homes' in user_data and user_data['homes']:
            self.home_id = user_data['homes'][0]['id']
            logger.info(f"Discovered Tado home ID: {self.home_id}")
        
        self._save_state()
        return user_data
    
    def get_mobile_devices(self) -> Optional[List[Dict]]:
        """Get all mobile devices for the home"""
        if not self.home_id:
            if not self.get_user_info():
                return None
        
        devices = self._api_request(f"/homes/{self.home_id}/mobileDevices")
        if devices is None:
            return None
        
        logger.debug(f"Found {len(devices)} mobile devices in Tado")
        return devices
    
    def get_presence_data(self, people_config: List[Dict]) -> Dict[str, Dict]:
        """
        Get presence data for configured people
        
        Args:
            people_config: List of people dicts with 'person' and 'tado_name' keys
            
        Returns:
            Dict mapping person -> {'from_tado': 0/1, 'ts': timestamp}
        """
        devices = self.get_mobile_devices()
        if devices is None:
            logger.warning("Could not get mobile devices from Tado")
            return {}
        
        # Build mapping from tado_name to person
        # Note: Tado mobile device names are often like "Nick's iPhone" while
        # tado_name might just be "Nick". We therefore do a tolerant match
        # where we allow substring matches in either direction.
        tado_name_to_person = {}
        for person_config in people_config:
            person = person_config.get('person')
            tado_name = person_config.get('tado_name')
            if person and tado_name:
                tado_name_to_person[tado_name.lower()] = person
        
        presence_data = {}
        current_time = time.time()
        
        for device in devices:
            name = device.get('name', '')
            settings = device.get('settings') or {}
            location = device.get('location') or {}
            
            # Check if geolocation is enabled
            if not settings.get('geoTrackingEnabled', False):
                continue
            
            # Get presence status
            at_home = location.get('atHome', False)
            
            # Map to configured person with tolerant matching
            name_lower = name.lower()
            person = tado_name_to_person.get(name_lower)
            if not person:
                for tado_name_lower, candidate_person in tado_name_to_person.items():
                    if tado_name_lower in name_lower or name_lower in tado_name_lower:
                        person = candidate_person
                        break
            
            if person:
                presence_data[person] = {
                    'from_tado': 1 if at_home else 0,
                    'ts': current_time
                }
                logger.debug(f"Tado presence: {person} = {at_home} (device name: {name})")
            else:
                logger.debug(f"Tado mobile device '{name}' (at_home={at_home}) did not match any configured tado_name")
        
        return presence_data

    def _notify_refresh_token_expired(self, details: str = "") -> None:
        """Send a local email when the Tado refresh token is no longer valid.

        This is driven by Tado error responses (e.g. expired_token), not by
        any local countdown of days.
        """
        # Avoid spamming: only send once per process
        if getattr(self, "_refresh_expiry_notified", False):
            return
        self._refresh_expiry_notified = True

        subject = "Tado presence refresh token expired - action required"
        body = textwrap.dedent(
            """
            The Tado refresh token used by the presence-monitoring service
            is no longer valid according to the Tado API.

            To restore Tado-based presence:

              1. Log in to quartz
              2. Run:

                   sudo python /home/nickc/code/electricity_monitoring/presence/tado_api.py --device-auth

              3. Follow the instructions printed by the script:
                   - Open the provided https://login.tado.com/oauth2/device URL
                   - Log in to your Tado account
                   - Approve access for the device

            The script will automatically update /etc/presence-monitoring.env
            and presence/state.json with fresh Tado tokens.

            After completing the above, restart the service:

                   sudo systemctl restart presence-monitoring

            Diagnostic details from the last refresh attempt:
            {details}
            """
        ).format(details=details or "<none>")

        try:
            logger.warning("Sending Tado refresh-token expiry notification email to local user 'nickc'")
            subprocess.run(
                ["mail", "-s", subject, "nickc"],
                input=body.encode("utf-8"),
                check=False,
            )
        except Exception as e:
            logger.warning(f"Failed to send Tado refresh-token expiry email: {e}")


def _update_env_with_tokens(access_token: str, refresh_token: Optional[str]) -> None:
    """Persist Tado tokens into /etc/presence-monitoring.env with backup.

    This is intended to be run interactively (typically with sudo) so that
    the presence-monitoring service can pick up fresh tokens without
    manual copy/paste. If the env file cannot be written, a warning is
    printed but tokens are still shown to the user.
    """
    env_file = "/etc/presence-monitoring.env"
    timestamp = time.strftime("%Y-%d-%m_%H%M")

    try:
        # Ensure env file exists and create backup first, as per project rules
        if os.path.exists(env_file):
            backup = f"{env_file}_{timestamp}.bak"
            shutil.copy2(env_file, backup)
        else:
            open(env_file, "a").close()

        tmp = env_file + ".tmp"
        with open(env_file, "r") as f_in, open(tmp, "w") as f_out:
            for line in f_in:
                if line.startswith("TADO_ACCESS_TOKEN=") or line.startswith("TADO_REFRESH_TOKEN="):
                    continue
                f_out.write(line)
            f_out.write(f"TADO_ACCESS_TOKEN={access_token}\n")
            if refresh_token:
                f_out.write(f"TADO_REFRESH_TOKEN={refresh_token}\n")
        os.replace(tmp, env_file)
        print(f"\nStored Tado tokens in {env_file} (backup created with suffix _{timestamp}.bak).")
    except Exception as e:
        print(f"\nWARNING: Could not update {env_file} automatically: {e}\n"
              "You may need to add TADO_ACCESS_TOKEN/TADO_REFRESH_TOKEN manually.")


def _update_state_with_tokens(access_token: str, refresh_token: Optional[str], expires_in: Optional[int]) -> None:
    """Persist Tado tokens into presence/state.json via TadoAPI.

    This ensures the presence-monitoring service, which prefers state
    over environment, will use the latest rotated refresh token.
    """
    try:
        client = TadoAPI("", "")
        client.access_token = access_token
        client.refresh_token = refresh_token
        # If expires_in is provided, use it; otherwise default to 1 hour
        ttl = expires_in or 3600
        client.token_expires_at = time.time() + ttl
        client._save_state()
        print(f"Stored Tado tokens in {client.state_file}.")
    except Exception as e:
        print(f"\nWARNING: Could not update Tado state file automatically: {e}")


def run_device_code_flow():
    """Run Tado device code flow to obtain an access token.

    This implements the flow described in Tado's support article
    "How do I authenticate to access the REST API" using the
    device_authorize and token endpoints on login.tado.com.
    """
    DEVICE_AUTH_URL = "https://login.tado.com/oauth2/device_authorize"
    DEVICE_TOKEN_URL = "https://login.tado.com/oauth2/token"
    DEVICE_CLIENT_ID = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"

    print("Starting Tado device authorization flow...")

    try:
        # Step 1: obtain device_code and user_code
        resp = requests.post(
            DEVICE_AUTH_URL,
            params={
                "client_id": DEVICE_CLIENT_ID,
                "scope": "offline_access",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Failed to start device authorization flow: {e}")
        return

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri")
    verification_uri_complete = data.get("verification_uri_complete")
    interval = int(data.get("interval", 5))
    expires_in = int(data.get("expires_in", 300))

    if not device_code or not verification_uri_complete:
        print("Device authorization response was missing required fields.")
        print(f"Response: {data}")
        return

    print("\n1) In a browser, open this URL and log in to your Tado account:")
    print(f"   {verification_uri_complete}")
    if user_code:
        print(f"   (User code should be prefilled, but if not, use: {user_code})")

    print("\n2) After confirming access in the browser, this script will poll Tado "
          "to obtain an access token.")
    print(f"   Polling every {interval}s for up to {expires_in}s...\n")
    print("Waiting 10 seconds before starting to poll so you can open the URL...")
    time.sleep(10)

    start_time = time.time()
    while True:
        if time.time() - start_time > expires_in:
            print("Device code has expired before authorization was completed.")
            return

        time.sleep(interval)

        try:
            token_resp = requests.post(
                DEVICE_TOKEN_URL,
                params={
                    "client_id": DEVICE_CLIENT_ID,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                },
                timeout=10,
            )

            # 200 -> success, 400 with error -> pending/slow_down/expired
            if token_resp.status_code == 200:
                token_data = token_resp.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token")
                expires_in = token_data.get("expires_in")
                print("\nSuccessfully obtained Tado access token.\n")
                if access_token:
                    print("Access token:\n")
                    print(access_token)
                if refresh_token:
                    print("\nRefresh token:\n")
                    print(refresh_token)
                if access_token:
                    _update_env_with_tokens(access_token, refresh_token)
                    _update_state_with_tokens(access_token, refresh_token, expires_in)
                return

            # Handle expected device-flow errors
            try:
                err_data = token_resp.json()
                error = err_data.get("error")
            except Exception:
                error = None

            if error in ("authorization_pending", "slow_down"):
                # Keep polling
                continue
            elif error == "expired_token":
                print("Device code has expired; please restart the device authorization flow.")
                return
            else:
                print(f"Unexpected error while polling for token: {token_resp.status_code} {err_data}")
                return

        except Exception as e:
            print(f"Error while polling for Tado token: {e}")
            return


def main():
    """Test the Tado API client or run device auth helper."""
    logging.basicConfig(level=logging.DEBUG)

    parser = argparse.ArgumentParser(description="Tado API test and device auth helper")
    parser.add_argument(
        "--device-auth",
        action="store_true",
        help="Run Tado device code flow to obtain an access token",
    )

    args = parser.parse_args()

    if args.device_auth:
        run_device_code_flow()
        return

    username = os.getenv('TADO_USERNAME')
    password = os.getenv('TADO_PASSWORD')
    access_token = os.getenv('TADO_ACCESS_TOKEN')
    
    if not access_token and (not username or not password):
        print("Please set TADO_ACCESS_TOKEN or TADO_USERNAME and TADO_PASSWORD environment variables")
        return
    
    client = TadoAPI(username or "", password or "")
    
    print("Getting user info...")
    user_info = client.get_user_info()
    if user_info:
        print(f"User: {user_info.get('name')}")
        print(f"Home ID: {client.home_id}")
    
    print("\nGetting mobile devices...")
    devices = client.get_mobile_devices()
    if devices:
        for device in devices:
            name = device.get('name')
            location = device.get('location') or {}
            settings = device.get('settings') or {}
            at_home = location.get('atHome', False)
            geo_enabled = settings.get('geoTrackingEnabled', False)
            print(f"  {name}: at_home={at_home}, geo_tracking={geo_enabled}")
    
    # Test presence mapping
    people_config = [
        {'person': 'nick', 'tado_name': 'Nick'},
        {'person': 'susan', 'tado_name': 'Susan'}
    ]
    
    print("\nGetting presence data...")
    presence = client.get_presence_data(people_config)
    for person, data in presence.items():
        print(f"  {person}: {data}")


if __name__ == '__main__':
    main()
