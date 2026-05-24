#!/usr/bin/env python3
"""
Home Assistant API client to get Tado presence data via HA integration
This bypasses Tado's API rate limits by using HA as a proxy
"""

import json
import logging
import os
import time
from typing import Dict, Optional, List
import requests

logger = logging.getLogger(__name__)


class HomeAssistantAPI:
    """Client for Home Assistant API to get Tado presence data"""
    
    def __init__(self, base_url: str = "http://homeassistant.local:8123", token: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.token = token or os.getenv('HA_TOKEN')
        
        if not self.token:
            logger.warning("No Home Assistant token provided - some endpoints may not work")
    
    def _api_request(self, endpoint: str) -> Optional[Dict]:
        """Make authenticated API request to Home Assistant"""
        try:
            url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
            headers = {}
            
            if self.token:
                headers['Authorization'] = f'Bearer {self.token}'
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Home Assistant API request failed for {endpoint}: {e}")
            return None
    
    def get_states(self) -> Optional[List[Dict]]:
        """Get all entity states from Home Assistant"""
        return self._api_request("/states")
    
    def get_tado_device_trackers(self) -> Dict[str, Dict]:
        """
        Get Tado device tracker entities from Home Assistant
        
        Returns:
            Dict mapping entity_id -> state info
        """
        states = self.get_states()
        if not states:
            return {}
        
        tado_trackers = {}
        for state in states:
            entity_id = state.get('entity_id', '')
            
            # Look for Tado device trackers
            if entity_id.startswith('device_tracker.') and 'tado' in entity_id.lower():
                tado_trackers[entity_id] = state
        
        return tado_trackers
    
    def get_person_entities(self) -> Dict[str, Dict]:
        """
        Get person entities from Home Assistant
        
        Returns:
            Dict mapping entity_id -> state info
        """
        states = self.get_states()
        if not states:
            return {}
        
        persons = {}
        for state in states:
            entity_id = state.get('entity_id', '')
            
            if entity_id.startswith('person.'):
                persons[entity_id] = state
        
        return persons
    
    def get_presence_data(self, people_config: List[Dict]) -> Dict[str, Dict]:
        """Get presence data for people with ha_person_entity or ha_device_tracker configured.

        Returns dict mapping person -> {'from_homeassistant': 0/1, 'ts': timestamp}.
        """
        states = self.get_states()
        if not states:
            return {}

        state_by_id = {s.get('entity_id'): s for s in states}
        current_time = time.time()
        presence_data = {}

        for person_config in people_config:
            person = person_config.get('person')
            if not person:
                continue
            entity_id = person_config.get('ha_person_entity') or person_config.get('ha_device_tracker')
            if not entity_id:
                continue
            state_info = state_by_id.get(entity_id)
            if not state_info:
                continue
            state = state_info.get('state', 'unknown').lower()
            at_home = 1 if state == 'home' else 0
            presence_data[person] = {'from_homeassistant': at_home, 'ts': current_time}
            logger.debug(f"HA presence: {person} = {at_home} (entity: {entity_id})")

        return presence_data
    
    def discover_entities(self):
        """Discover relevant entities for presence tracking"""
        print("Discovering Home Assistant entities...")
        
        # Get person entities
        persons = self.get_person_entities()
        if persons:
            print(f"\nFound {len(persons)} person entities:")
            for entity_id, state_info in persons.items():
                name = state_info.get('attributes', {}).get('friendly_name', entity_id)
                state = state_info.get('state', 'unknown')
                print(f"  {entity_id}: {name} (state: {state})")
        
        # Get Tado device trackers
        tado_trackers = self.get_tado_device_trackers()
        if tado_trackers:
            print(f"\nFound {len(tado_trackers)} Tado device trackers:")
            for entity_id, state_info in tado_trackers.items():
                name = state_info.get('attributes', {}).get('friendly_name', entity_id)
                state = state_info.get('state', 'unknown')
                print(f"  {entity_id}: {name} (state: {state})")
        
        # Get all device trackers for reference
        states = self.get_states()
        all_trackers = [s for s in states if s.get('entity_id', '').startswith('device_tracker.')]
        print(f"\nTotal device trackers available: {len(all_trackers)}")
        
        return {
            'persons': persons,
            'tado_trackers': tado_trackers,
            'all_trackers': all_trackers
        }


def main():
    """Test the Home Assistant API client"""
    logging.basicConfig(level=logging.DEBUG)
    
    ha_token = os.getenv('HA_TOKEN')
    if not ha_token:
        print("Warning: HA_TOKEN not set - some data may not be available")
    
    client = HomeAssistantAPI()
    
    print("Testing Home Assistant connection...")
    entities = client.discover_entities()
    
    # Test presence mapping
    people_config = [
        {'person': 'nick', 'tado_name': 'Nick', 'ha_person_entity': 'person.nick'},
        {'person': 'susan', 'tado_name': 'Susan', 'ha_person_entity': 'person.susan'},
        {'person': 'charlie', 'tado_name': 'Charlie', 'ha_person_entity': 'person.charlie'},
        {'person': 'archie', 'tado_name': 'Archie', 'ha_person_entity': 'person.archie'},
        {'person': 'mo', 'tado_name': 'Mo', 'ha_person_entity': 'person.mo'}
    ]
    
    print("\nGetting presence data...")
    presence = client.get_presence_data(people_config)
    for person, data in presence.items():
        print(f"  {person}: {data}")


if __name__ == '__main__':
    main()