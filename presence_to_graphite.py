#!/usr/bin/env python3
"""
Presence to Graphite Integration
Combines WiFi device detection and Tado geofencing to track who's home

Usage:
    python presence_to_graphite.py [--discover] [--once]
    
Options:
    --discover: Run WiFi scan, show devices and suggest mappings
    --once: Run one poll cycle and exit (for testing)
"""

import asyncio
import argparse
import json
import logging
import os
import time
import yaml
from typing import Dict, Set, Optional, List, Tuple

# Import our presence modules
from presence.wifi_scan import scan_network, normalize_mac
from presence.tado_api import TadoAPI
from presence.homeassistant_api import HomeAssistantAPI
from presence.mac_learning import IntelligentMacLearner
from graphite_helper import send_metrics

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PresenceMonitor:
    """Main presence monitoring coordinator"""
    
    def __init__(self, config_file: str = "presence/people_config.yaml"):
        self.config_file = config_file
        self.config = None
        self.tado_client = None
        self.state_file = "presence/state.json"
        self.mac_learning_state_file = "presence/mac_learning_state.json"
        self.state = {}
        self.mac_learner = None
        
        # Load configuration
        self._load_config()
        self._load_state()
        
        # Initialize MAC learning system
        self._init_mac_learner()
        
        # Initialize Tado client if enabled
        if self.config['tado']['enabled']:
            self._init_tado_client()
        
        # Initialize Home Assistant client if enabled
        if self.config.get('homeassistant', {}).get('enabled', False):
            self._init_homeassistant_client()
    
    def _load_config(self):
        """Load configuration from YAML file"""
        try:
            with open(self.config_file, 'r') as f:
                self.config = yaml.safe_load(f)
            logger.info(f"Loaded configuration from {self.config_file}")
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_file}: {e}")
            raise
    
    def _load_state(self):
        """Load persistent state from JSON file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    self.state = json.load(f)
            else:
                self.state = {
                    'last_seen_wifi': {},
                    'last_seen_person_wifi': {},
                    'suggestions': {}
                }
        except Exception as e:
            logger.warning(f"Could not load state file: {e}")
            self.state = {
                'last_seen_wifi': {},
                'last_seen_person_wifi': {},
                'suggestions': {}
            }
    
    def _save_state(self):
        """Save persistent state to JSON file"""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file + '.tmp', 'w') as f:
                json.dump(self.state, f, indent=2)
            os.replace(self.state_file + '.tmp', self.state_file)
        except Exception as e:
            logger.warning(f"Could not save state file: {e}")
    
    def _init_tado_client(self):
        """Initialize Tado API client with credentials from environment"""
        username = os.getenv('TADO_USERNAME')
        password = os.getenv('TADO_PASSWORD')
        
        if not username or not password:
            logger.warning("Tado enabled but TADO_USERNAME/TADO_PASSWORD not set")
            return
        
        try:
            self.tado_client = TadoAPI(username, password, self.state_file)
            logger.info("Initialized Tado API client")
        except Exception as e:
            logger.error(f"Failed to initialize Tado client: {e}")
    
    def _init_homeassistant_client(self):
        """Initialize Home Assistant API client"""
        try:
            ha_config = self.config['homeassistant']
            base_url = ha_config.get('base_url', 'http://homeassistant.local:8123')
            token_env_var = ha_config.get('token_env_var', 'HA_TOKEN')
            token = os.getenv(token_env_var)
            
            if not token:
                logger.warning(f"Home Assistant enabled but {token_env_var} not set")
                return
            
            self.ha_client = HomeAssistantAPI(base_url, token)
            logger.info("Initialized Home Assistant API client")
        except Exception as e:
            logger.error(f"Failed to initialize Home Assistant client: {e}")
    
    def _init_mac_learner(self):
        """Initialize intelligent MAC learning system"""
        try:
            self.mac_learner = IntelligentMacLearner(self.mac_learning_state_file)
            logger.debug("Initialized MAC learning system")
        except Exception as e:
            logger.error(f"Failed to initialize MAC learner: {e}")
    
    def _build_person_mappings(self) -> Dict:
        """Build mapping dictionaries from config"""
        mac_to_person = {}
        hostname_hints = {}
        
        for person_config in self.config['people']:
            person = person_config['person']
            
            # Map MAC addresses
            for mac in person_config.get('wifi_macs', []):
                mac_normalized = normalize_mac(mac)
                if mac_normalized:
                    mac_to_person[mac_normalized] = person
            
            # Map hostname hints
            hints = person_config.get('wifi_hostnames', [])
            if hints:
                hostname_hints[person] = [hint.lower() for hint in hints]
        
        return {
            'mac_to_person': mac_to_person,
            'hostname_hints': hostname_hints
        }
    
    def _scan_wifi(self) -> Dict:
        """Perform WiFi network scan"""
        cidr = self.config['wifi']['cidr']
        logger.debug(f"Scanning WiFi network: {cidr}")
        
        try:
            result = scan_network(cidr)
            logger.debug(f"WiFi scan found {len(result['devices'])} devices")
            return result
        except Exception as e:
            logger.error(f"WiFi scan failed: {e}")
            return {'devices': [], 'present_macs': set()}
    
    def _scan_wifi_with_fingerprinting(self) -> Dict:
        """Perform WiFi network scan with device fingerprinting"""
        cidr = self.config['wifi']['cidr']
        logger.debug(f"Scanning WiFi network with fingerprinting: {cidr}")
        
        try:
            result = scan_network(cidr, fingerprint_iphones=True)
            logger.debug(f"WiFi scan found {len(result['devices'])} devices")
            return result
        except Exception as e:
            logger.error(f"WiFi scan failed: {e}")
            return {'devices': [], 'present_macs': set()}
    
    def _get_tado_presence(self) -> Dict[str, Dict]:
        """Get presence data from Tado API"""
        if not self.tado_client:
            return {}
        
        try:
            people_config = self.config['people']
            presence_data = self.tado_client.get_presence_data(people_config)
            logger.debug(f"Tado presence data: {presence_data}")
            return presence_data
        except Exception as e:
            logger.error(f"Failed to get Tado presence: {e}")
            return {}
    
    def _get_homeassistant_presence(self) -> Dict[str, Dict]:
        """Get presence data from Home Assistant API"""
        if not hasattr(self, 'ha_client') or not self.ha_client:
            return {}
        
        try:
            # Build people config with HA device trackers
            people_with_ha = []
            for person_config in self.config['people']:
                ha_tracker = person_config.get('ha_device_tracker')
                if ha_tracker:
                    people_with_ha.append({
                        'person': person_config['person'],
                        'ha_device_tracker': ha_tracker
                    })
            
            # Get HA states
            states = self.ha_client.get_states()
            if not states:
                return {}
            
            presence_data = {}
            current_time = time.time()
            
            for person_data in people_with_ha:
                person = person_data['person']
                entity_id = person_data['ha_device_tracker']
                
                # Find the entity in states
                for state in states:
                    if state.get('entity_id') == entity_id:
                        state_value = state.get('state', 'unknown').lower()
                        at_home = 1 if state_value == 'home' else 0
                        
                        presence_data[person] = {
                            'from_homeassistant': at_home,
                            'ts': current_time
                        }
                        logger.debug(f"HA presence: {person} = {at_home} (entity: {entity_id})")
                        break
            
            return presence_data
        except Exception as e:
            logger.error(f"Failed to get Home Assistant presence: {e}")
            return {}
    
    def _update_wifi_state(self, scan_result: Dict, mappings: Dict):
        """Update WiFi last-seen state"""
        current_time = time.time()
        present_macs = scan_result['present_macs']
        devices = scan_result['devices']
        
        # Update last_seen for present MACs
        for mac in present_macs:
            self.state['last_seen_wifi'][mac] = current_time
        
        # Update person last-seen based on MAC mappings
        mac_to_person = mappings['mac_to_person']
        hostname_hints = mappings['hostname_hints']
        
        for mac in present_macs:
            person = mac_to_person.get(mac)
            if person:
                self.state['last_seen_person_wifi'][person] = current_time
        
        # Check hostname hints for people without MAC mappings
        for device in devices:
            hostname = device.get('hostname', '') or ''
            hostname = hostname.lower()
            if hostname:
                for person, hints in hostname_hints.items():
                    for hint in hints:
                        if hint in hostname:
                            self.state['last_seen_person_wifi'][person] = current_time
                            break
    
    def _compute_presence(self, tado_presence: Dict[str, Dict], ha_presence: Dict[str, Dict] = None) -> Dict[str, Dict]:
        """Compute final presence for all people"""
        if ha_presence is None:
            ha_presence = {}
            
        current_time = time.time()
        grace_seconds = self.config['wifi']['offline_grace_seconds']
        presence_data = {}
        
        for person_config in self.config['people']:
            person = person_config['person']
            
            # WiFi presence (within grace period)
            last_wifi = self.state['last_seen_person_wifi'].get(person, 0)
            from_wifi = 1 if (current_time - last_wifi) <= grace_seconds else 0
            
            # Tado presence
            tado_data = tado_presence.get(person, {})
            from_tado = tado_data.get('from_tado', 0)
            
            # Home Assistant presence
            ha_data = ha_presence.get(person, {})
            from_homeassistant = ha_data.get('from_homeassistant', 0)
            
            # Combined presence (OR logic of all sources)
            is_home = max(from_wifi, from_tado, from_homeassistant)
            
            presence_data[person] = {
                'from_wifi': from_wifi,
                'from_tado': from_tado,
                'from_homeassistant': from_homeassistant,
                'is_home': is_home,
                'last_wifi': last_wifi
            }
        
        return presence_data
    
    def _send_metrics(self, presence_data: Dict[str, Dict], scan_result: Dict):
        """Send presence metrics to Graphite"""
        metrics = []
        prefix = self.config['metrics']['prefix']
        
        # Per-person metrics
        for person, data in presence_data.items():
            base_metric = f"{prefix}.{person}"
            metrics.extend([
                (f"{base_metric}.from_wifi", data['from_wifi']),
                (f"{base_metric}.from_tado", data['from_tado']),
                (f"{base_metric}.from_homeassistant", data['from_homeassistant']),
                (f"{base_metric}.is_home", data['is_home'])
            ])
        
        # Aggregate metrics
        count_home = sum(data['is_home'] for data in presence_data.values())
        anyone_home = 1 if count_home > 0 else 0
        devices_present = len(scan_result['present_macs'])
        
        metrics.extend([
            (f"{prefix}.count_home", count_home),
            (f"{prefix}.anyone_home", anyone_home),
            (f"{prefix}.wifi.devices_present_count", devices_present)
        ])
        
        # Send to Graphite
        graphite_host = self.config['graphite']['host']
        graphite_port = self.config['graphite']['port']
        
        try:
            count = send_metrics(graphite_host, graphite_port, metrics)
            logger.info(f"Sent {count} presence metrics to Graphite")
            
            # Log summary
            people_home = [person for person, data in presence_data.items() if data['is_home']]
            if people_home:
                logger.info(f"People home: {', '.join(people_home)} ({count_home} total)")
            else:
                logger.info("Nobody home")
                
        except Exception as e:
            logger.error(f"Failed to send metrics to Graphite: {e}")
    
    def _suggest_mappings(self, scan_result: Dict, tado_presence: Dict[str, Dict]):
        """Suggest MAC mappings based on arrival/departure correlation"""
        current_time = time.time()
        
        # Look for new arrivals in Tado data
        for person, tado_data in tado_presence.items():
            if tado_data.get('from_tado') == 1:
                # Person just arrived via Tado - look for new MACs
                for mac in scan_result['present_macs']:
                    last_seen = self.state['last_seen_wifi'].get(mac, 0)
                    # If this MAC was not seen recently but is present now
                    if current_time - last_seen > 1800:  # 30 minutes ago
                        suggestion_key = f"{person}:{mac}"
                        self.state['suggestions'][suggestion_key] = self.state['suggestions'].get(suggestion_key, 0) + 1
        
        # Log suggestions that have multiple correlations
        for key, count in self.state['suggestions'].items():
            if count >= 2:
                person, mac = key.split(':', 1)
                logger.info(f"Suggestion: map MAC {mac} to {person} (co-arrived {count} times)")
    
    def discover(self, fingerprint: bool = False):
        """Run discovery mode - scan and show devices"""
        print("Discovering devices on network...")
        if fingerprint:
            print("(Including device fingerprinting - this will take longer)")
        
        scan_result = self._scan_wifi_with_fingerprinting() if fingerprint else self._scan_wifi()
        
        if not scan_result['devices']:
            print("No devices found.")
            return
        
        print(f"\nFound {len(scan_result['devices'])} devices:\n")
        
        if fingerprint:
            print(f"{'IP Address':<16} {'MAC Address':<18} {'Hostname':<20} {'IPv6':<20} {'Device Type':<12} {'OS Guess'}")
            print("-" * 120)
        else:
            print(f"{'IP Address':<16} {'MAC Address':<18} {'Hostname':<20} {'IPv6'}")
            print("-" * 80)
        
        for device in scan_result['devices']:
            ip = device['ip']
            mac = device['mac']
            hostname = device['hostname'] or 'N/A'
            ipv6 = device.get('ipv6', 'N/A') or 'N/A'
            
            if fingerprint and 'fingerprint' in device:
                fp = device['fingerprint']
                device_type = fp.get('device_type', 'Unknown')
                os_guess = fp.get('os_guess', 'Unknown')
                print(f"{ip:<16} {mac:<18} {hostname:<20} {ipv6:<20} {device_type:<12} {os_guess}")
            else:
                print(f"{ip:<16} {mac:<18} {hostname:<20} {ipv6}")
        
        # Show current mappings
        mappings = self._build_person_mappings()
        mac_to_person = mappings['mac_to_person']
        
        if mac_to_person:
            print(f"\nConfigured MAC mappings:")
            for mac, person in mac_to_person.items():
                print(f"  {mac} -> {person}")
        
        # Show legacy suggestions
        suggestions = self.state.get('suggestions', {})
        if suggestions:
            print(f"\nLegacy suggested mappings (add to people_config.yaml):")
            for key, count in suggestions.items():
                if count >= 2:
                    person, mac = key.split(':', 1)
                    print(f"  {mac} -> {person} (confidence: {count})")
        
        # Show intelligent MAC learning suggestions
        if self.mac_learner:
            recent_suggestions = self.mac_learner.get_recent_suggestions(hours=24)
            if recent_suggestions:
                print(f"\nIntelligent MAC Learning Suggestions (last 24h):")
                for event in recent_suggestions:
                    print(f"\n{self.mac_learner.format_suggestion_for_user(event)}")
        
        print(f"\nTo configure presence monitoring:")
        print(f"1. Edit {self.config_file}")
        print(f"2. Add MAC addresses to wifi_macs for each person")
        print(f"3. Test with: python {__file__} --once")
    
    async def poll_once(self):
        """Run one poll cycle"""
        logger.info("Running presence poll cycle...")
        
        # Get data from all sources
        scan_result = self._scan_wifi()
        tado_presence = self._get_tado_presence()
        ha_presence = self._get_homeassistant_presence()
        
        # Build mappings and update state
        mappings = self._build_person_mappings()
        self._update_wifi_state(scan_result, mappings)
        
        # Compute final presence
        presence_data = self._compute_presence(tado_presence, ha_presence)
        
        # Send metrics
        self._send_metrics(presence_data, scan_result)
        
        # Update suggestions (legacy method)
        self._suggest_mappings(scan_result, tado_presence)
        
        # Intelligent MAC learning
        self._run_mac_learning(scan_result, presence_data)
        
        # Save state
        self._save_state()
        
        return presence_data
    
    def _run_mac_learning(self, scan_result: Dict, presence_data: Dict[str, Dict]):
        """Run intelligent MAC learning analysis"""
        if not self.mac_learner:
            return
        
        try:
            # Build current MAC mappings for learning system
            mappings = self._build_person_mappings()
            mac_to_person = mappings['mac_to_person']
            
            # Run learning analysis
            learning_events = self.mac_learner.learn_from_scan(
                scan_result['devices'],
                presence_data,
                mac_to_person
            )
            
            # Log any suggestions
            for event in learning_events:
                if event.action == 'suggest':
                    suggestion_msg = self.mac_learner.format_suggestion_for_user(event)
                    logger.info(f"MAC Learning Suggestion:\n{suggestion_msg}")
                elif event.action == 'auto_add':
                    suggestion_msg = self.mac_learner.format_suggestion_for_user(event)
                    logger.warning(f"High Confidence MAC Learning Suggestion:\n{suggestion_msg}")
            
        except Exception as e:
            logger.error(f"MAC learning failed: {e}")
    
    async def main_loop(self):
        """Main monitoring loop"""
        logger.info("Starting presence monitoring")
        logger.info(f"WiFi CIDR: {self.config['wifi']['cidr']}")
        logger.info(f"WiFi scan interval: {self.config['wifi']['scan_interval_seconds']}s")
        logger.info(f"WiFi offline grace: {self.config['wifi']['offline_grace_seconds']}s")
        logger.info(f"Tado enabled: {self.config['tado']['enabled']}")
        logger.info(f"Home Assistant enabled: {self.config.get('homeassistant', {}).get('enabled', False)}")
        
        last_wifi_scan = 0
        last_tado_poll = 0
        last_ha_poll = 0
        last_metric_send = 0
        
        wifi_interval = self.config['wifi']['scan_interval_seconds']
        tado_interval = self.config['tado']['poll_interval_seconds']
        ha_interval = self.config.get('homeassistant', {}).get('poll_interval_seconds', 60)
        metric_interval = 5  # Send metrics every 5 seconds
        
        try:
            while True:
                current_time = time.time()
                
                # WiFi scan
                if current_time - last_wifi_scan >= wifi_interval:
                    scan_result = self._scan_wifi()
                    mappings = self._build_person_mappings()
                    self._update_wifi_state(scan_result, mappings)
                    last_wifi_scan = current_time
                else:
                    scan_result = {'devices': [], 'present_macs': set()}
                
                # Tado poll
                if current_time - last_tado_poll >= tado_interval:
                    tado_presence = self._get_tado_presence()
                    last_tado_poll = current_time
                else:
                    tado_presence = {}
                
                # Home Assistant poll
                if current_time - last_ha_poll >= ha_interval:
                    ha_presence = self._get_homeassistant_presence()
                    last_ha_poll = current_time
                else:
                    ha_presence = {}
                
                # Send metrics and run learning
                if current_time - last_metric_send >= metric_interval:
                    presence_data = self._compute_presence(tado_presence, ha_presence)
                    self._send_metrics(presence_data, scan_result)
                    
                    # Run MAC learning analysis if we have fresh scan data
                    if scan_result['devices']:
                        self._run_mac_learning(scan_result, presence_data)
                    
                    last_metric_send = current_time
                
                # Save state periodically
                self._save_state()
                
                # Sleep until next cycle
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")


def main():
    parser = argparse.ArgumentParser(description='Presence to Graphite Integration')
    parser.add_argument('--discover', action='store_true', 
                       help='Discover devices and show mapping suggestions')
    parser.add_argument('--fingerprint', action='store_true',
                       help='Include device fingerprinting (use with --discover)')
    parser.add_argument('--once', action='store_true',
                       help='Run one poll cycle and exit (for testing)')
    args = parser.parse_args()
    
    monitor = PresenceMonitor()
    
    if args.discover:
        monitor.discover(fingerprint=args.fingerprint)
    elif args.once:
        asyncio.run(monitor.poll_once())
    else:
        asyncio.run(monitor.main_loop())


if __name__ == '__main__':
    main()