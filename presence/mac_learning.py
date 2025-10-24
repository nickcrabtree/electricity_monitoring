#!/usr/bin/env python3
"""
Intelligent MAC address learning system for presence monitoring.

Correlates WiFi scan data with Home Assistant presence to automatically
suggest new MAC mappings when devices change addresses due to randomization.

Uses multiple identification methods:
- IPv6 neighbor discovery and pattern matching
- Device fingerprinting (OS, open ports, services)
- Hostname patterns and consistency
- Temporal correlation with Home Assistant presence changes
"""

import logging
import json
import time
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict
import re

logger = logging.getLogger(__name__)


@dataclass
class DeviceFingerprint:
    """Device fingerprint for identification across MAC changes"""
    os_guess: Optional[str] = None
    device_type: Optional[str] = None  # iPhone, Android, macOS, Windows, Linux
    open_ports: List[Dict] = None  # port, protocol, service
    vendor: Optional[str] = None
    ipv6_suffix: Optional[str] = None  # Last 64 bits of IPv6 (often stable)
    hostname_pattern: Optional[str] = None
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    confidence: float = 0.0  # 0.0-1.0 confidence in fingerprint accuracy
    
    def __post_init__(self):
        if self.open_ports is None:
            self.open_ports = []


@dataclass 
class MacLearningEvent:
    """Record of a potential MAC learning opportunity"""
    timestamp: float
    person: str
    old_mac: Optional[str]  # May be None for new person
    new_mac: str
    evidence_score: float  # 0.0-1.0 confidence score
    evidence: Dict  # Details about why this mapping is suggested
    action: str  # 'suggest', 'auto_add', 'ignore'
    fingerprint: Optional[DeviceFingerprint] = None


class MacLearningState:
    """Persistent state for MAC learning system"""
    
    def __init__(self, state_file: str):
        self.state_file = state_file
        self.device_fingerprints: Dict[str, DeviceFingerprint] = {}  # mac -> fingerprint
        self.person_fingerprints: Dict[str, List[DeviceFingerprint]] = {}  # person -> [fingerprints]
        self.learning_events: List[MacLearningEvent] = []
        self.presence_history: Dict[str, List[Tuple[float, bool]]] = {}  # person -> [(timestamp, is_home)]
        self.mac_history: Dict[str, List[Tuple[float, str]]] = {}  # person -> [(timestamp, mac)]
        self.suggestions_made: Set[str] = set()  # Track suggested mappings to avoid spam
        self.load()
    
    def load(self):
        """Load state from JSON file"""
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
            
            # Convert loaded data back to dataclasses
            if 'device_fingerprints' in data:
                for mac, fp_data in data['device_fingerprints'].items():
                    self.device_fingerprints[mac] = DeviceFingerprint(**fp_data)
            
            if 'person_fingerprints' in data:
                for person, fp_list in data['person_fingerprints'].items():
                    self.person_fingerprints[person] = [DeviceFingerprint(**fp) for fp in fp_list]
            
            if 'learning_events' in data:
                for event_data in data['learning_events']:
                    fp_data = event_data.get('fingerprint')
                    fingerprint = DeviceFingerprint(**fp_data) if fp_data else None
                    event_data['fingerprint'] = fingerprint
                    self.learning_events.append(MacLearningEvent(**event_data))
            
            # Load simple data structures as-is
            self.presence_history = data.get('presence_history', {})
            self.mac_history = data.get('mac_history', {})
            self.suggestions_made = set(data.get('suggestions_made', []))
            
            logger.debug(f"Loaded MAC learning state: {len(self.device_fingerprints)} device fingerprints, "
                        f"{len(self.learning_events)} learning events")
                        
        except Exception as e:
            logger.debug(f"Could not load MAC learning state: {e}")
            # Start with empty state
    
    def save(self):
        """Save state to JSON file"""
        try:
            data = {
                'device_fingerprints': {mac: asdict(fp) for mac, fp in self.device_fingerprints.items()},
                'person_fingerprints': {person: [asdict(fp) for fp in fps] 
                                      for person, fps in self.person_fingerprints.items()},
                'learning_events': [asdict(event) for event in self.learning_events],
                'presence_history': self.presence_history,
                'mac_history': self.mac_history,
                'suggestions_made': list(self.suggestions_made)
            }
            
            # Write atomically
            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            import os
            os.replace(temp_file, self.state_file)
            
        except Exception as e:
            logger.error(f"Failed to save MAC learning state: {e}")


def extract_ipv6_suffix(ipv6_addr: str) -> Optional[str]:
    """Extract the last 64 bits of IPv6 address (device identifier part)"""
    if not ipv6_addr or '::' not in ipv6_addr:
        return None
    
    try:
        # Remove zone ID if present (e.g., %eth0)
        addr = ipv6_addr.split('%')[0]
        
        # For link-local addresses, extract the interface identifier
        if addr.startswith('fe80::'):
            # Interface identifier is everything after fe80::
            suffix = addr[6:]  # Remove 'fe80::'
            return suffix
        
        # For other IPv6 addresses, take the last 64 bits (4 groups)
        parts = addr.split(':')
        if len(parts) >= 4:
            return ':'.join(parts[-4:])
            
    except Exception as e:
        logger.debug(f"Failed to extract IPv6 suffix from {ipv6_addr}: {e}")
    
    return None


def fingerprint_similarity(fp1: DeviceFingerprint, fp2: DeviceFingerprint) -> float:
    """Calculate similarity score between two device fingerprints (0.0-1.0)"""
    score = 0.0
    weight_sum = 0.0
    
    # Device type match (high weight)
    if fp1.device_type and fp2.device_type:
        weight_sum += 3.0
        if fp1.device_type == fp2.device_type:
            score += 3.0
    
    # OS guess match (medium-high weight)
    if fp1.os_guess and fp2.os_guess:
        weight_sum += 2.0
        if fp1.os_guess.lower() in fp2.os_guess.lower() or fp2.os_guess.lower() in fp1.os_guess.lower():
            score += 2.0
    
    # IPv6 suffix match (high weight - very stable)
    if fp1.ipv6_suffix and fp2.ipv6_suffix:
        weight_sum += 4.0
        if fp1.ipv6_suffix == fp2.ipv6_suffix:
            score += 4.0
    
    # Hostname pattern match (medium weight)
    if fp1.hostname_pattern and fp2.hostname_pattern:
        weight_sum += 1.5
        if fp1.hostname_pattern.lower() in fp2.hostname_pattern.lower():
            score += 1.5
    
    # Open ports similarity (lower weight, can change)
    if fp1.open_ports and fp2.open_ports:
        weight_sum += 1.0
        ports1 = {(p['port'], p['protocol']) for p in fp1.open_ports}
        ports2 = {(p['port'], p['protocol']) for p in fp2.open_ports}
        
        if ports1 and ports2:
            intersection = len(ports1 & ports2)
            union = len(ports1 | ports2)
            port_similarity = intersection / union if union > 0 else 0
            score += port_similarity * 1.0
    
    return score / weight_sum if weight_sum > 0 else 0.0


def create_device_fingerprint(device: Dict, additional_data: Dict = None) -> DeviceFingerprint:
    """Create a device fingerprint from scan data"""
    fp = DeviceFingerprint()
    
    # Extract data from device info
    fp.hostname_pattern = device.get('hostname')
    
    # IPv6 suffix extraction
    if device.get('ipv6'):
        fp.ipv6_suffix = extract_ipv6_suffix(device['ipv6'])
    
    # Extract fingerprint data if available
    if 'fingerprint' in device:
        scan_fp = device['fingerprint']
        fp.os_guess = scan_fp.get('os_guess')
        fp.device_type = scan_fp.get('device_type')
        fp.open_ports = scan_fp.get('open_ports', [])
        fp.vendor = scan_fp.get('vendor')
    
    # Set timestamps
    current_time = time.time()
    fp.first_seen = current_time
    fp.last_seen = current_time
    
    # Calculate initial confidence based on available data
    confidence = 0.0
    if fp.device_type:
        confidence += 0.3
    if fp.ipv6_suffix:
        confidence += 0.4  # IPv6 is very reliable
    if fp.hostname_pattern:
        confidence += 0.2
    if fp.open_ports:
        confidence += 0.1
    
    fp.confidence = min(confidence, 1.0)
    
    return fp


def analyze_presence_correlation(
    learning_state: MacLearningState,
    person: str, 
    new_mac: str,
    ha_presence_changed: bool = False,
    wifi_presence_changed: bool = False
) -> float:
    """
    Analyze correlation between presence changes and new MAC appearances
    Returns correlation score (0.0-1.0)
    """
    current_time = time.time()
    correlation_window = 300  # 5 minutes
    
    score = 0.0
    
    # Check if this person recently changed presence in HA but not WiFi
    if person in learning_state.presence_history:
        recent_presence = learning_state.presence_history[person]
        
        # Look for recent HA presence changes
        recent_ha_changes = [
            (ts, present) for ts, present in recent_presence 
            if current_time - ts < correlation_window
        ]
        
        if recent_ha_changes:
            # If HA shows person as home but WiFi didn't detect them, this could be a new MAC
            latest_ha_presence = recent_ha_changes[-1][1]  # Most recent presence state
            
            if latest_ha_presence and not wifi_presence_changed:
                score += 0.4  # Strong indicator
            elif ha_presence_changed and wifi_presence_changed:
                score += 0.2  # Consistent change
    
    # Check timing correlation - did new MAC appear when person arrived?
    mac_appearance_score = 0.0
    if new_mac not in [mac for _, mac in learning_state.mac_history.get(person, [])]:
        # This is truly a new MAC for this person
        mac_appearance_score += 0.3
    
    return min(score + mac_appearance_score, 1.0)


class IntelligentMacLearner:
    """Main MAC learning system"""
    
    def __init__(self, state_file: str):
        self.state = MacLearningState(state_file)
        self.learning_threshold = 0.6  # Minimum evidence score to suggest mapping
        self.auto_add_threshold = 0.85  # Score to automatically add mapping
    
    def update_device_fingerprints(self, scan_results: List[Dict]):
        """Update device fingerprints from scan results"""
        for device in scan_results:
            mac = device['mac']
            if not mac:
                continue
                
            # Create or update fingerprint
            if mac in self.state.device_fingerprints:
                # Update existing fingerprint
                fp = self.state.device_fingerprints[mac]
                fp.last_seen = time.time()
                
                # Update with new data if available
                if 'fingerprint' in device and device['fingerprint']:
                    scan_fp = device['fingerprint']
                    if scan_fp.get('device_type') and not fp.device_type:
                        fp.device_type = scan_fp['device_type']
                    if scan_fp.get('os_guess') and not fp.os_guess:
                        fp.os_guess = scan_fp['os_guess']
                    # Add new ports (don't overwrite, ports can change)
                    if scan_fp.get('open_ports'):
                        existing_ports = {(p['port'], p['protocol']) for p in fp.open_ports}
                        for port in scan_fp['open_ports']:
                            port_key = (port['port'], port['protocol'])
                            if port_key not in existing_ports:
                                fp.open_ports.append(port)
            else:
                # Create new fingerprint
                fp = create_device_fingerprint(device)
                self.state.device_fingerprints[mac] = fp
    
    def update_presence_history(self, person_presence: Dict[str, Dict]):
        """Update presence history for correlation analysis"""
        current_time = time.time()
        max_history = 100  # Keep last 100 presence changes per person
        
        for person, presence_data in person_presence.items():
            if person not in self.state.presence_history:
                self.state.presence_history[person] = []
            
            # Check if presence changed from last known state
            history = self.state.presence_history[person]
            is_home = presence_data.get('is_home', False)
            
            # Only add if state changed or it's the first entry
            if not history or history[-1][1] != is_home:
                history.append((current_time, is_home))
                
                # Trim old history
                if len(history) > max_history:
                    history = history[-max_history:]
                    self.state.presence_history[person] = history
    
    def find_mac_candidates_for_person(
        self, 
        person: str, 
        current_macs: Set[str],
        unknown_devices: List[Dict]
    ) -> List[Tuple[str, float, Dict]]:
        """
        Find MAC address candidates for a person who appears offline via WiFi
        but online via Home Assistant
        
        Returns list of (mac, confidence_score, evidence) tuples
        """
        candidates = []
        
        # Get person's known fingerprints
        person_fps = self.state.person_fingerprints.get(person, [])
        if not person_fps:
            # No fingerprints yet, can't make good suggestions
            return candidates
        
        # Check each unknown device against person's fingerprints
        for device in unknown_devices:
            mac = device['mac']
            if mac in current_macs:  # Already assigned to someone
                continue
            
            # Create fingerprint for this device
            device_fp = create_device_fingerprint(device)
            
            # Compare against person's known fingerprints
            max_similarity = 0.0
            best_match_evidence = {}
            
            for known_fp in person_fps:
                similarity = fingerprint_similarity(device_fp, known_fp)
                if similarity > max_similarity:
                    max_similarity = similarity
                    best_match_evidence = {
                        'fingerprint_similarity': similarity,
                        'matching_device_type': device_fp.device_type == known_fp.device_type,
                        'matching_ipv6_suffix': device_fp.ipv6_suffix == known_fp.ipv6_suffix,
                        'matching_hostname_pattern': (
                            device_fp.hostname_pattern and known_fp.hostname_pattern and
                            known_fp.hostname_pattern.lower() in device_fp.hostname_pattern.lower()
                        ),
                        'device_fingerprint': asdict(device_fp),
                        'matched_against': asdict(known_fp)
                    }
            
            # Add presence correlation analysis
            correlation_score = analyze_presence_correlation(
                self.state, person, mac, 
                ha_presence_changed=True,  # Assuming we're called because HA shows them home
                wifi_presence_changed=False  # But WiFi doesn't see them
            )
            
            # Combined evidence score
            evidence_score = (max_similarity * 0.7) + (correlation_score * 0.3)
            
            if evidence_score >= self.learning_threshold:
                evidence = {
                    **best_match_evidence,
                    'presence_correlation': correlation_score,
                    'combined_score': evidence_score,
                    'device_info': device
                }
                
                candidates.append((mac, evidence_score, evidence))
        
        # Sort by confidence score (highest first)
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates
    
    def learn_from_scan(
        self, 
        scan_results: List[Dict],
        current_person_presence: Dict[str, Dict],
        current_mac_mappings: Dict[str, str]  # mac -> person
    ) -> List[MacLearningEvent]:
        """
        Main learning function - analyze scan results and suggest new mappings
        
        Args:
            scan_results: List of devices from network scan
            current_person_presence: Current presence state for each person
            current_mac_mappings: Current MAC to person mappings
            
        Returns:
            List of learning events (suggestions)
        """
        # Update our state
        self.update_device_fingerprints(scan_results)
        self.update_presence_history(current_person_presence)
        
        current_time = time.time()
        learning_events = []
        
        # Get current known MACs
        known_macs = set(current_mac_mappings.keys())
        
        # Find devices with unknown MACs
        unknown_devices = [
            device for device in scan_results 
            if device['mac'] and device['mac'] not in known_macs
        ]
        
        logger.debug(f"MAC learning: {len(unknown_devices)} unknown devices, "
                    f"{len(current_person_presence)} people tracked")
        
        # For each person, check if they need new MAC mappings
        for person, presence_data in current_person_presence.items():
            is_home_ha = presence_data.get('from_homeassistant', False)
            is_home_wifi = presence_data.get('from_wifi', False)
            
            # Key scenario: Person is home according to HA but not detected via WiFi
            if is_home_ha and not is_home_wifi:
                logger.debug(f"Person {person} shows home via HA but not WiFi - looking for new MAC")
                
                candidates = self.find_mac_candidates_for_person(
                    person, known_macs, unknown_devices
                )
                
                for mac, evidence_score, evidence in candidates:
                    # Check if we already suggested this mapping recently
                    suggestion_key = f"{person}:{mac}"
                    if suggestion_key in self.state.suggestions_made:
                        continue
                    
                    # Determine action based on confidence
                    action = 'suggest'
                    if evidence_score >= self.auto_add_threshold:
                        action = 'auto_add'
                    
                    # Create learning event
                    event = MacLearningEvent(
                        timestamp=current_time,
                        person=person,
                        old_mac=None,  # This is a new mapping, not replacing existing
                        new_mac=mac,
                        evidence_score=evidence_score,
                        evidence=evidence,
                        action=action,
                        fingerprint=self.state.device_fingerprints.get(mac)
                    )
                    
                    learning_events.append(event)
                    self.state.learning_events.append(event)
                    self.state.suggestions_made.add(suggestion_key)
                    
                    # Update person's fingerprint history
                    if person not in self.state.person_fingerprints:
                        self.state.person_fingerprints[person] = []
                    
                    if mac in self.state.device_fingerprints:
                        self.state.person_fingerprints[person].append(
                            self.state.device_fingerprints[mac]
                        )
                    
                    # Only suggest the best candidate to avoid spam
                    break
        
        # Save state
        self.state.save()
        
        return learning_events
    
    def get_recent_suggestions(self, hours: int = 24) -> List[MacLearningEvent]:
        """Get learning suggestions from the last N hours"""
        cutoff_time = time.time() - (hours * 3600)
        return [
            event for event in self.state.learning_events 
            if event.timestamp >= cutoff_time and event.action in ['suggest', 'auto_add']
        ]
    
    def format_suggestion_for_user(self, event: MacLearningEvent) -> str:
        """Format a learning event as a user-friendly suggestion"""
        evidence = event.evidence
        confidence_pct = int(event.evidence_score * 100)
        
        msg = f"MAC Learning Suggestion (confidence: {confidence_pct}%):\n"
        msg += f"  Add MAC {event.new_mac} to person '{event.person}'\n"
        
        if evidence.get('device_info'):
            device = evidence['device_info']
            msg += f"  Device: {device['ip']} - {device.get('hostname', 'N/A')}\n"
        
        reasons = []
        if evidence.get('matching_device_type'):
            msg += f"  - Device type matches previous devices\n"
        if evidence.get('matching_ipv6_suffix'):
            msg += f"  - IPv6 suffix matches (very reliable)\n"
        if evidence.get('matching_hostname_pattern'):
            msg += f"  - Hostname pattern matches\n"
        if evidence.get('presence_correlation', 0) > 0.3:
            msg += f"  - Timing correlates with Home Assistant presence\n"
        
        if event.action == 'auto_add':
            msg += "  *** HIGH CONFIDENCE - Consider auto-adding ***\n"
        
        msg += f"  Command: Add '{event.new_mac}' to wifi_macs for {event.person} in people_config.yaml"
        
        return msg


def test_mac_learning():
    """Test the MAC learning system"""
    import tempfile
    import os
    
    # Create temporary state file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        state_file = f.name
    
    try:
        learner = IntelligentMacLearner(state_file)
        
        # Simulate scan results
        scan_results = [
            {
                'ip': '192.168.86.45',
                'mac': 'BE:80:47:8F:9F:78',
                'hostname': 'iPhone.lan',
                'fingerprint': {'device_type': 'iPhone', 'os_guess': 'iOS'}
            },
            {
                'ip': '192.168.86.50',
                'mac': 'AA:BB:CC:DD:EE:FF',  # New unknown MAC
                'hostname': 'iPhone.lan',
                'fingerprint': {'device_type': 'iPhone', 'os_guess': 'iOS'},
                'ipv6': 'fe80::a8bb:ccff:fedd:eeff'
            }
        ]
        
        # Current mappings
        current_mappings = {
            'BE:80:47:8F:9F:78': 'nick'  # Nick's known MAC
        }
        
        # Presence data - Nick is home via HA but not detected via current WiFi MAC
        presence_data = {
            'nick': {
                'from_homeassistant': True,
                'from_wifi': False,  # This triggers learning
                'is_home': True
            }
        }
        
        # Learn from scan
        events = learner.learn_from_scan(scan_results, presence_data, current_mappings)
        
        print(f"Generated {len(events)} learning events:")
        for event in events:
            print("\n" + learner.format_suggestion_for_user(event))
    
    finally:
        # Clean up
        try:
            os.unlink(state_file)
        except:
            pass


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    test_mac_learning()