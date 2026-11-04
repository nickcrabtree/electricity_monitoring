"""Tests for tools/towel_rail_cloud_check.py: warn when Smart Life / HA lose the towel rail.

On 2026-11-02 the towel rail dropped off the Tuya cloud: HA kept a plausible "off" with last_changed frozen at 16:23
UTC while blackpi2's direct LAN poll saw it switch several times. The check compares the two and emails Nick after
30 minutes of disagreement, then once a day while it lasts, and once more when it recovers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

import towel_rail_cloud_check as trc  # noqa: E402

NOW = 1_800_000_000.0
MIN = 60


def local(*changes, until=NOW):
    """Raw local points every 30 s; changes are (minutes_ago, value) in time order."""
    points = []
    for i, (ago, value) in enumerate(changes):
        end = until if i == len(changes) - 1 else until - changes[i + 1][0] * MIN
        t = until - ago * MIN
        while t < end:
            points.append((t, value))
            t += 30
    return points


def ha(state, changed_minutes_ago):
    return {'state': state, 'last_changed': NOW - changed_minutes_ago * MIN}


class TestClassify:
    def test_agreeing_sources_are_ok(self):
        status, _ = trc.classify(local((300, 0)), ha('off', 400), NOW)
        assert status == 'ok'

    def test_disagreeing_state_is_a_fault(self):
        status, detail = trc.classify(local((300, 0), (60, 1)), ha('off', 400), NOW)
        assert status == 'fault'
        assert 'on' in detail

    def test_unavailable_in_ha_is_a_fault(self):
        status, _ = trc.classify(local((300, 0)), ha('unavailable', 10), NOW)
        assert status == 'fault'

    def test_missed_transitions_with_matching_state_are_a_fault(self):
        # 2026-11-02 pattern: HA frozen at "off" while the rail went on and off again locally.
        status, _ = trc.classify(local((300, 0), (200, 1), (100, 0)), ha('off', 400), NOW)
        assert status == 'fault'

    def test_ha_change_slightly_before_local_poll_is_ok(self):
        # HA hears of a change within seconds; the local poll may record it up to a poll later.
        status, _ = trc.classify(local((300, 0), (60, 1)), ha('on', 61), NOW)
        assert status == 'ok'

    def test_no_recent_local_data_is_unknown(self):
        status, _ = trc.classify(local((300, 0), until=NOW - 30 * MIN), ha('off', 400), NOW)
        assert status == 'unknown'

    def test_fault_persists_when_missed_transitions_leave_the_window(self):
        # A missed on/off pair more than 6 h ago is out of the lookback; HA unchanged since the fault began
        # must not count as recovered.
        status, _ = trc.classify(local((360, 0)), ha('off', 2000), NOW, fault_since=NOW - 1000 * MIN)
        assert status == 'fault'

    def test_ha_change_after_fault_began_allows_recovery(self):
        status, _ = trc.classify(local((360, 0), (5, 1)), ha('on', 5), NOW, fault_since=NOW - 1000 * MIN)
        assert status == 'ok'

    def test_ha_unreachable_is_unknown(self):
        status, _ = trc.classify(local((300, 0)), None, NOW)
        assert status == 'unknown'


class FakeMail:
    def __init__(self):
        self.sent, self.works = [], True

    def __call__(self, subject, body):
        self.sent.append((subject, body))
        return self.works


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


def episode(tmp_path):
    mail, clock = FakeMail(), Clock()
    return trc.Episode(str(tmp_path / 'state'), now=clock, send=mail), mail, clock


class TestEpisode:
    def test_alert_only_after_thirty_minutes(self, tmp_path):
        ep, mail, clock = episode(tmp_path)
        ep.observe('fault', 'x')
        clock.t += 29 * MIN
        ep.observe('fault', 'x')
        assert mail.sent == []
        clock.t += 2 * MIN
        ep.observe('fault', 'x')
        assert len(mail.sent) == 1
        assert 'Smart Life' in mail.sent[0][1]

    def test_reminder_once_a_day_while_it_lasts(self, tmp_path):
        ep, mail, clock = episode(tmp_path)
        ep.observe('fault', 'x')
        clock.t += 31 * MIN
        ep.observe('fault', 'x')
        clock.t += 23 * 60 * MIN
        ep.observe('fault', 'x')
        assert len(mail.sent) == 1
        clock.t += 2 * 60 * MIN
        ep.observe('fault', 'x')
        assert len(mail.sent) == 2

    def test_recovery_email_after_an_alert(self, tmp_path):
        ep, mail, clock = episode(tmp_path)
        ep.observe('fault', 'x')
        clock.t += 31 * MIN
        ep.observe('fault', 'x')
        ep.observe('ok', '')
        assert len(mail.sent) == 2
        assert 'RECOVERED' in mail.sent[1][0]

    def test_short_fault_sends_nothing(self, tmp_path):
        ep, mail, clock = episode(tmp_path)
        ep.observe('fault', 'x')
        clock.t += 10 * MIN
        ep.observe('ok', '')
        clock.t += 30 * MIN
        ep.observe('fault', 'x')
        assert mail.sent == []

    def test_unknown_neither_alerts_nor_clears(self, tmp_path):
        ep, mail, clock = episode(tmp_path)
        ep.observe('fault', 'x')
        clock.t += 20 * MIN
        ep.observe('unknown', '')
        clock.t += 11 * MIN
        ep.observe('fault', 'x')
        assert len(mail.sent) == 1

    def test_failed_send_is_retried(self, tmp_path):
        ep, mail, clock = episode(tmp_path)
        mail.works = False
        ep.observe('fault', 'x')
        clock.t += 31 * MIN
        ep.observe('fault', 'x')
        mail.works = True
        clock.t += 15 * MIN
        ep.observe('fault', 'x')
        assert len(mail.sent) == 2


def test_parse_render_drops_nulls():
    payload = [{'target': 't', 'datapoints': [[None, 1], [1.0, 2], [0.0, 3]]}]
    assert trc.parse_render(payload) == [(2.0, 1.0), (3.0, 0.0)]


def test_parse_ha_converts_last_changed_to_epoch():
    state = trc.parse_ha({'state': 'off', 'last_changed': '2026-11-02T16:23:52.832956+00:00'})
    assert state['state'] == 'off'
    assert abs(state['last_changed'] - 1793636632.83) < 1


def test_episode_reports_when_the_fault_began(tmp_path):
    ep, _, clock = episode(tmp_path)
    assert ep.since() is None
    ep.observe('fault', 'x')
    assert ep.since() == NOW
    clock.t += MIN
    ep.observe('ok', '')
    assert ep.since() is None
