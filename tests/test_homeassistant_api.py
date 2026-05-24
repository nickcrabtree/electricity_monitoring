"""Tests for HomeAssistantAPI.get_presence_data() presence logic."""

import pytest
from unittest.mock import MagicMock
from presence.homeassistant_api import HomeAssistantAPI


def _make_client(states):
    client = HomeAssistantAPI.__new__(HomeAssistantAPI)
    client.get_states = MagicMock(return_value=states)
    return client


PEOPLE = [
    {'person': 'alice', 'ha_device_tracker': 'device_tracker.alice_iphone'},
    {'person': 'bob',   'ha_person_entity':  'person.bob'},
    {'person': 'carol', 'ha_device_tracker': 'device_tracker.carol_iphone'},
]


class TestGetPresenceData:
    def test_home_state_returns_1(self):
        states = [{'entity_id': 'device_tracker.alice_iphone', 'state': 'home'}]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert result['alice']['from_homeassistant'] == 1

    def test_not_home_returns_0(self):
        states = [{'entity_id': 'device_tracker.alice_iphone', 'state': 'not_home'}]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert result['alice']['from_homeassistant'] == 0

    def test_unknown_state_returns_0(self):
        states = [{'entity_id': 'device_tracker.alice_iphone', 'state': 'unknown'}]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert result['alice']['from_homeassistant'] == 0

    def test_ha_person_entity_key_supported(self):
        states = [{'entity_id': 'person.bob', 'state': 'home'}]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert result['bob']['from_homeassistant'] == 1

    def test_person_without_entity_excluded(self):
        people = [{'person': 'dave'}]  # no ha_device_tracker or ha_person_entity
        client = _make_client([])
        result = client.get_presence_data(people)
        assert 'dave' not in result

    def test_entity_not_in_states_excluded(self):
        states = [{'entity_id': 'device_tracker.other', 'state': 'home'}]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert 'alice' not in result

    def test_multiple_people_resolved(self):
        states = [
            {'entity_id': 'device_tracker.alice_iphone', 'state': 'home'},
            {'entity_id': 'person.bob', 'state': 'not_home'},
            {'entity_id': 'device_tracker.carol_iphone', 'state': 'home'},
        ]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert result['alice']['from_homeassistant'] == 1
        assert result['bob']['from_homeassistant'] == 0
        assert result['carol']['from_homeassistant'] == 1

    def test_empty_states_returns_empty(self):
        client = _make_client([])
        result = client.get_presence_data(PEOPLE)
        assert result == {}

    def test_get_states_returns_none_returns_empty(self):
        client = _make_client(None)
        result = client.get_presence_data(PEOPLE)
        assert result == {}

    def test_state_is_case_insensitive(self):
        states = [{'entity_id': 'device_tracker.alice_iphone', 'state': 'Home'}]
        client = _make_client(states)
        result = client.get_presence_data(PEOPLE)
        assert result['alice']['from_homeassistant'] == 1
