"""Tests for meter_watchdog: whole-house meter outage detection and the alert episode state machine."""

import meter_watchdog as mw

T0 = 1_800_000_000
HOUR = 3600


def points(values, step=10, start=T0):
    return [(start + i * step, v) for i, v in enumerate(values)]


class TestParseRender:
    def test_drops_nulls_and_returns_time_value_pairs(self):
        payload = [{'target': 'x', 'datapoints': [[None, T0], [720.0, T0 + 10], [None, T0 + 20], [630.0, T0 + 30]]}]
        assert mw.parse_render(payload) == [(T0 + 10, 720.0), (T0 + 30, 630.0)]

    def test_empty_payload(self):
        assert mw.parse_render([]) == []


class TestClassify:
    def test_normal_data_is_ok(self):
        power = points([720, 630, 720, 810])
        pulses = points([100, 102, 104, 106])
        assert mw.classify(power, pulses) == 'ok'

    def test_half_null_data_is_still_ok(self):
        # the 30 min fetch returns about half nulls (5 s retention against 10 s reports); that is normal
        assert mw.classify(points([720, 630]), points([100, 102])) == 'ok'

    def test_no_points_at_all_is_silent(self):
        assert mw.classify([], []) == 'silent'

    def test_flat_pulse_count_is_zero(self):
        # the 2026-09-24 and October 2026 outages: ESP32 reporting, LM393 sensor seeing no flashes
        assert mw.classify(points([0, 0, 0, 0]), points([500, 500, 500, 500])) == 'zero'

    def test_flat_pulse_count_wins_over_nonzero_power(self):
        assert mw.classify(points([720, 720]), points([500, 500, 500])) == 'zero'

    def test_all_zero_power_without_pulse_series_is_zero(self):
        assert mw.classify(points([0, 0, 0]), []) == 'zero'

    def test_single_pulse_point_does_not_count_as_flat(self):
        assert mw.classify(points([720, 630]), points([500])) == 'ok'


class FakeMailer:
    def __init__(self):
        self.sent = []

    def __call__(self, subject, body):
        self.sent.append((subject, body))
        return True


def make_episode(tmp_path):
    clock = {'now': float(T0)}
    mailer = FakeMailer()
    episode = mw.Episode(state_dir=str(tmp_path / 'state'), now=lambda: clock['now'], send=mailer)
    return episode, clock, mailer


class TestEpisode:
    def test_short_fault_sends_nothing(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        episode.observe('zero', 'detail')
        clock['now'] += mw.ALERT_AFTER_SECONDS - 60
        episode.observe('zero', 'detail')
        assert mailer.sent == []

    def test_sustained_fault_alerts_once(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        episode.observe('zero', 'detail')
        clock['now'] += mw.ALERT_AFTER_SECONDS
        episode.observe('zero', 'detail')
        clock['now'] += 300
        episode.observe('zero', 'detail')
        assert len(mailer.sent) == 1
        subject, body = mailer.sent[0]
        assert 'meter' in subject.lower()
        assert 'zero' in subject or 'no pulses' in subject
        assert 'detail' in body

    def test_recovery_after_alert_sends_recovered(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        episode.observe('silent', 'detail')
        clock['now'] += mw.ALERT_AFTER_SECONDS
        episode.observe('silent', 'detail')
        clock['now'] += HOUR
        episode.observe('ok', 'back')
        assert len(mailer.sent) == 2
        assert 'RECOVERED' in mailer.sent[1][0]
        assert '90 minutes' in mailer.sent[1][1]

    def test_recovery_without_alert_is_silent(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        episode.observe('zero', 'detail')
        clock['now'] += 600
        episode.observe('ok', 'back')
        assert mailer.sent == []

    def test_flapping_fault_hits_the_daily_ceiling(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        for _ in range(3):
            episode.observe('zero', 'detail')
            clock['now'] += mw.ALERT_AFTER_SECONDS
            episode.observe('zero', 'detail')
            clock['now'] += 300
            episode.observe('ok', 'back')
            clock['now'] += 300
        # one alert, one RECOVERED; the later flaps are inside the 24 h ceiling
        assert [s for s, _ in mailer.sent if 'RECOVERED' not in s] and len(mailer.sent) == 2

    def test_long_fault_gets_a_daily_reminder(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        episode.observe('zero', 'detail')
        clock['now'] += mw.ALERT_AFTER_SECONDS
        episode.observe('zero', 'detail')
        clock['now'] += mw.REMIND_EVERY_SECONDS - 300
        episode.observe('zero', 'detail')
        assert len(mailer.sent) == 1
        clock['now'] += 600
        episode.observe('zero', 'detail')
        assert len(mailer.sent) == 2
        assert 'still' in mailer.sent[1][0].lower()

    def test_state_survives_a_new_instance(self, tmp_path):
        episode, clock, mailer = make_episode(tmp_path)
        episode.observe('zero', 'detail')
        clock['now'] += mw.ALERT_AFTER_SECONDS
        again = mw.Episode(state_dir=str(tmp_path / 'state'), now=lambda: clock['now'], send=mailer)
        again.observe('zero', 'detail')
        assert len(mailer.sent) == 1

    def test_failed_send_does_not_mark_alerted(self, tmp_path):
        clock = {'now': float(T0)}
        calls = []

        def failing(subject, body):
            calls.append(subject)
            return False

        episode = mw.Episode(state_dir=str(tmp_path / 'state'), now=lambda: clock['now'], send=failing)
        episode.observe('zero', 'detail')
        clock['now'] += mw.ALERT_AFTER_SECONDS
        episode.observe('zero', 'detail')
        clock['now'] += 300
        episode.observe('zero', 'detail')
        assert len(calls) == 2


class TestDescribe:
    def test_zero_detail_names_the_sensor_and_last_advance(self):
        power = points([0, 0, 0])
        pulses = points([500, 500, 500])
        text = mw.describe('zero', power, pulses, last_pulse_advance=T0 - 2 * HOUR)
        assert 'LM393' in text or 'sensor' in text
        assert 'pulse' in text

    def test_silent_detail_mentions_the_board(self):
        text = mw.describe('silent', [], [], last_pulse_advance=None)
        assert 'mpy-esp32' in text
