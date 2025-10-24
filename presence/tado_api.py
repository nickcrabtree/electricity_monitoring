#!/usr/bin/env python3
"""
Tado API client for geofencing presence data
"""

import json
import logging
import os
import time
from typing import Dict, Optional, List
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
    
    # Public client credentials for mobile app (commonly used)
    CLIENT_ID = "tado-web-app"
    CLIENT_SECRET = "wZaRN7rpjn3FoNyF5IFuxg9uMzYJcvOoQ8QWiIqS3hfk6gLhVlG57j5YNoZL2Rtc"
    
    def __init__(self, username: str, password: str, state_file: Optional[str] = None):
        self.username = username
        self.password = password
        self.state_file = state_file or "presence/state.json"
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = 0
        self.home_id = None
        self.user_info = None
        
        # Load cached tokens if available
        self._load_state()
    
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
        
        # Add 5-minute buffer before expiry
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
        """Refresh access token using refresh token"""
        if not self.refresh_token:
            return False
        
        try:
            logger.debug("Refreshing Tado access token")
            
            data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token
            }
            
            auth = HTTPBasicAuth(self.CLIENT_ID, self.CLIENT_SECRET)
            
            response = requests.post(self.AUTH_URL, data=data, auth=auth, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            if 'refresh_token' in token_data:
                self.refresh_token = token_data['refresh_token']
            
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = time.time() + expires_in
            
            logger.debug("Successfully refreshed Tado token")
            self._save_state()
            return True
            
        except Exception as e:
            logger.warning(f"Token refresh failed: {e}")
            return False
    
    def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid access token"""
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
        
        try:
            url = f"{self.BASE_URL}{endpoint}"
            headers = {'Authorization': f'Bearer {self.access_token}'}
            
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
            settings = device.get('settings', {})
            location = device.get('location', {})
            
            # Check if geolocation is enabled
            if not settings.get('geoTrackingEnabled', False):
                continue
            
            # Get presence status
            at_home = location.get('atHome', False)
            
            # Map to configured person
            person = tado_name_to_person.get(name.lower())
            if person:
                presence_data[person] = {
                    'from_tado': 1 if at_home else 0,
                    'ts': current_time
                }
                logger.debug(f"Tado presence: {person} = {at_home}")
        
        return presence_data


def main():
    """Test the Tado API client"""
    logging.basicConfig(level=logging.DEBUG)
    
    username = os.getenv('TADO_USERNAME')
    password = os.getenv('TADO_PASSWORD')
    
    if not username or not password:
        print("Please set TADO_USERNAME and TADO_PASSWORD environment variables")
        return
    
    client = TadoAPI(username, password)
    
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
            at_home = device.get('location', {}).get('atHome', False)
            geo_enabled = device.get('settings', {}).get('geoTrackingEnabled', False)
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