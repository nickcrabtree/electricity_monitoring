"""Tests that polled Tado / Home Assistant results persist between polls.

The main loop polls Tado every ``tado.poll_interval_seconds`` and HA every
``homeassistant.poll_interval_seconds`` but computes and sends presence every
few seconds.  A polled result must therefore be reused on the cycles between
polls, and dropped only once it is older than the staleness window
(``PresenceMonitor.SOURCE_STALE_FACTOR`` poll intervals), so that a dead API
cannot pin somebody "home" indefinitely.
"""

from presence_to_graphite import PresenceMonitor

TADO_INTERVAL = 300
HA_INTERVAL = 60


def _make_monitor():
    monitor = PresenceMonitor.__new__(PresenceMonitor)
    monitor.config = {
        'wifi': {'offline_grace_seconds': 300},
        'tado': {'enabled': True, 'poll_interval_seconds': TADO_INTERVAL},
        'homeassistant': {'enabled': True, 'poll_interval_seconds': HA_INTERVAL},
        'people': [{'person': 'nick'}, {'person': 'susan'}],
    }
    monitor.state = {'last_seen_person_wifi': {}}
    monitor._init_source_cache()
    return monitor


def _compute(monitor, now):
    tado, ha = monitor._recent_source_presence(now)
    return monitor._compute_presence(tado, ha)


class TestTadoCache:
    def test_result_is_reused_between_polls(self):
        m = _make_monitor()
        m._record_source_poll('tado', {'nick': {'from_tado': 1, 'ts': 0}}, now=0)
        presence = _compute(m, now=100)
        assert presence['nick']['from_tado'] == 1
        assert presence['nick']['is_home'] == 1

    def test_result_expires_after_stale_window(self):
        m = _make_monitor()
        m._record_source_poll('tado', {'nick': {'from_tado': 1, 'ts': 0}}, now=0)
        stale_at = TADO_INTERVAL * PresenceMonitor.SOURCE_STALE_FACTOR + 1
        assert _compute(m, now=stale_at - 2)['nick']['from_tado'] == 1
        assert _compute(m, now=stale_at)['nick']['from_tado'] == 0

    def test_failed_poll_keeps_previous_result_until_stale(self):
        m = _make_monitor()
        m._record_source_poll('tado', {'nick': {'from_tado': 1, 'ts': 0}}, now=0)
        # A failed poll returns {}; it must not wipe the last good result.
        m._record_source_poll('tado', {}, now=TADO_INTERVAL)
        assert _compute(m, now=TADO_INTERVAL + 5)['nick']['from_tado'] == 1
        stale_at = TADO_INTERVAL * PresenceMonitor.SOURCE_STALE_FACTOR + 1
        assert _compute(m, now=stale_at)['nick']['from_tado'] == 0

    def test_new_poll_replaces_previous_result(self):
        m = _make_monitor()
        m._record_source_poll('tado', {'nick': {'from_tado': 1, 'ts': 0}}, now=0)
        m._record_source_poll('tado', {'nick': {'from_tado': 0, 'ts': 300}}, now=TADO_INTERVAL)
        assert _compute(m, now=TADO_INTERVAL + 5)['nick']['from_tado'] == 0


class TestHomeAssistantCache:
    def test_result_is_reused_between_polls(self):
        m = _make_monitor()
        m._record_source_poll('homeassistant', {'susan': {'from_homeassistant': 1, 'ts': 0}}, now=0)
        presence = _compute(m, now=30)
        assert presence['susan']['from_homeassistant'] == 1
        assert presence['susan']['is_home'] == 1

    def test_result_expires_after_stale_window(self):
        m = _make_monitor()
        m._record_source_poll('homeassistant', {'susan': {'from_homeassistant': 1, 'ts': 0}}, now=0)
        stale_at = HA_INTERVAL * PresenceMonitor.SOURCE_STALE_FACTOR + 1
        assert _compute(m, now=stale_at)['susan']['from_homeassistant'] == 0

    def test_sources_are_independent(self):
        m = _make_monitor()
        m._record_source_poll('tado', {'nick': {'from_tado': 1, 'ts': 0}}, now=0)
        m._record_source_poll('homeassistant', {'susan': {'from_homeassistant': 1, 'ts': 0}}, now=0)
        # HA window (120 s) has passed, Tado window (600 s) has not.
        presence = _compute(m, now=200)
        assert presence['nick']['from_tado'] == 1
        assert presence['susan']['from_homeassistant'] == 0
