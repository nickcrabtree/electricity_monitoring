import asyncio
import json
import logging
import time

import pytest

import tuya_local_to_graphite as tl
from tuya_local_to_graphite import (
    load_local_device_overrides,
    merge_local_device_overrides,
)


def write_overrides(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


def test_load_local_device_overrides_accepts_complete_device(tmp_path):
    path = tmp_path / 'tuya_local_devices.json'
    write_overrides(
        path,
        {
            'devices': [
                {
                    'id': 'device-id',
                    'name': 'Ensuite shower pump',
                    'ip': '192.168.1.155',
                    'key': '0123456789abcdef',
                    'version': '3.4',
                }
            ]
        },
    )

    assert load_local_device_overrides(str(path)) == {
        'device-id': {
            'name': 'Ensuite shower pump',
            'ip': '192.168.1.155',
            'key': '0123456789abcdef',
            'version': '3.4',
        }
    }


def test_load_local_device_overrides_rejects_missing_or_invalid_keys(tmp_path, caplog):
    path = tmp_path / 'tuya_local_devices.json'
    private_key = 'not-a-local-key'
    write_overrides(
        path,
        {
            'devices': [
                {
                    'id': 'missing-key',
                    'ip': '192.168.1.155',
                    'version': '3.4',
                },
                {
                    'id': 'short-key',
                    'name': 'Short key device',
                    'ip': '192.168.1.156',
                    'key': private_key,
                    'version': '3.4',
                },
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        assert load_local_device_overrides(str(path)) == {}

    assert private_key not in caplog.text


def test_load_local_device_overrides_rejects_missing_or_insecure_file(tmp_path):
    missing_path = tmp_path / 'missing.json'
    assert load_local_device_overrides(str(missing_path)) == {}

    insecure_path = tmp_path / 'insecure.json'
    insecure_path.write_text('{"devices": []}')
    insecure_path.chmod(0o644)

    assert load_local_device_overrides(str(insecure_path)) == {}


def test_merge_local_device_overrides_keeps_discovered_metadata():
    discovered = {
        'device-id': {
            'name': 'Unknown',
            'ip': '192.168.1.155',
            'version': '3.3',
            'mac': 'aa:bb:cc:dd:ee:ff',
        }
    }
    overrides = {
        'device-id': {
            'name': 'Ensuite shower pump',
            'ip': '192.168.1.155',
            'key': '0123456789abcdef',
            'version': '3.4',
        }
    }

    assert merge_local_device_overrides(discovered, overrides) == {
        'device-id': {
            'name': 'Ensuite shower pump',
            'ip': '192.168.1.155',
            'key': '0123456789abcdef',
            'version': '3.4',
            'mac': 'aa:bb:cc:dd:ee:ff',
        }
    }


class TestNextTick:
    """The poller runs on a fixed beat aligned to multiples of the interval, and never fires catch-up rounds."""

    def test_on_schedule_keeps_the_beat(self):
        assert tl.next_tick(previous_tick=100.0, now=101.3, interval=5) == 105.0

    def test_fell_behind_jumps_to_next_future_multiple(self):
        # a rescan took 23 s: no burst of catch-up rounds
        assert tl.next_tick(previous_tick=100.0, now=123.0, interval=5) == 125.0

    def test_exactly_on_the_next_beat_moves_to_the_one_after(self):
        assert tl.next_tick(previous_tick=100.0, now=105.0, interval=5) == 110.0

    def test_clock_stepped_back_resyncs_instead_of_sleeping_long(self):
        assert tl.next_tick(previous_tick=1000.0, now=500.5, interval=5) == 505.0

    def test_first_tick_is_the_next_multiple(self):
        assert tl.first_tick(now=101.3, interval=5) == 105.0
        assert tl.first_tick(now=105.0, interval=5) == 110.0


def _metrics_for(device_id):
    return [(f'home.electricity.tuya.{device_id}.power_watts', 1.0)]


@pytest.fixture
def sent(monkeypatch):
    batches = []

    def fake_send(server, port, metrics):
        batches.append(list(metrics))
        return len(metrics)

    monkeypatch.setattr(tl, 'send_metrics', fake_send)
    return batches


class TestPollRound:
    def test_all_devices_answer_and_nothing_is_left_in_flight(self, monkeypatch, sent):
        async def fake(device, device_id, **kwargs):
            return _metrics_for(device_id)

        monkeypatch.setattr(tl, 'get_device_metrics', fake)
        inflight = {}
        count = asyncio.run(tl.poll_round({'a': object(), 'b': object()}, inflight, budget=1))
        assert count == 2
        assert inflight == {}
        assert sorted(m[0] for m in sent[0]) == [
            'home.electricity.tuya.a.power_watts',
            'home.electricity.tuya.b.power_watts',
        ]

    def test_round_passes_single_attempt_and_the_short_timeout(self, monkeypatch, sent):
        seen = []

        async def fake(device, device_id, **kwargs):
            seen.append(kwargs)
            return _metrics_for(device_id)

        monkeypatch.setattr(tl, 'get_device_metrics', fake)
        asyncio.run(tl.poll_round({'a': object()}, {}, budget=1, device_timeout=3.5))
        assert seen == [{'retries': 1, 'timeout': 3.5, 'bridge': None}]

    def test_hung_device_does_not_hold_up_the_others_and_is_not_started_twice(self, monkeypatch, sent):
        calls = {'slow': 0}
        release = {}

        async def fake(device, device_id, **kwargs):
            if device_id == 'slow':
                calls['slow'] += 1
                await release['event'].wait()
            return _metrics_for(device_id)

        monkeypatch.setattr(tl, 'get_device_metrics', fake)

        async def scenario():
            release['event'] = asyncio.Event()
            inflight = {}
            devices = {'fast': object(), 'slow': object()}
            started = time.monotonic()
            first = await tl.poll_round(devices, inflight, budget=0.05)
            first_took = time.monotonic() - started
            second = await tl.poll_round(devices, inflight, budget=0.05)
            still_in_flight = set(inflight)
            release['event'].set()
            await asyncio.sleep(0)
            third = await tl.poll_round(devices, inflight, budget=0.05)
            calls_after_third = calls['slow']
            fourth = await tl.poll_round(devices, inflight, budget=0.05)
            return first, first_took, second, still_in_flight, third, calls_after_third, fourth, set(inflight)

        first, first_took, second, still_in_flight, third, calls_after_third, fourth, after = asyncio.run(scenario())
        assert first == 1 and first_took < 0.5  # only the fast device, on time
        assert second == 1  # the hung one is skipped, not launched a second time
        assert still_in_flight == {'slow'}
        assert third == 2  # 'fast' again, plus the late result from 'slow'
        assert calls_after_third == 1  # still the one original call: no duplicate point in the same batch
        assert fourth == 2 and calls['slow'] == 2  # next round polls 'slow' afresh
        assert after == set()

    def test_failed_device_task_is_logged_and_dropped(self, monkeypatch, sent, caplog):
        async def fake(device, device_id, **kwargs):
            if device_id == 'bad':
                raise RuntimeError('boom')
            return _metrics_for(device_id)

        monkeypatch.setattr(tl, 'get_device_metrics', fake)
        inflight = {}
        with caplog.at_level(logging.ERROR):
            count = asyncio.run(tl.poll_round({'good': object(), 'bad': object()}, inflight, budget=1))
        assert count == 1
        assert inflight == {}
        assert 'boom' in caplog.text

    def test_nothing_to_send_returns_zero(self, monkeypatch, sent):
        async def fake(device, device_id, **kwargs):
            return []

        monkeypatch.setattr(tl, 'get_device_metrics', fake)
        assert asyncio.run(tl.poll_round({'a': object()}, {}, budget=1)) == 0
        assert sent == []


class _FlakyDevice:
    def __init__(self, exc=None, delay=0.0):
        self.calls = 0
        self.exc = exc
        self.delay = delay

    def status(self):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return {'dps': {'1': True, '19': 100}}


class TestGetDeviceMetricsAttempts:
    def test_single_attempt_gives_up_at_once_without_backoff(self):
        device = _FlakyDevice(exc=OSError('no route'))
        started = time.monotonic()
        result = asyncio.run(tl.get_device_metrics(device, 'dev1', retries=1, timeout=0.5))
        assert result == []
        assert device.calls == 1
        assert time.monotonic() - started < 1.0  # the default 3 attempts would sleep 2 s then 4 s

    def test_timeout_argument_is_honoured(self):
        device = _FlakyDevice(delay=0.3)
        started = time.monotonic()
        result = asyncio.run(tl.get_device_metrics(device, 'dev1', retries=1, timeout=0.05))
        assert result == []
        assert time.monotonic() - started < 0.3 + 0.2  # asyncio.run also waits for the worker thread

    def test_default_still_retries_for_once_and_discover(self, monkeypatch):
        device = _FlakyDevice(exc=OSError('no route'))
        slept = []

        async def no_sleep(seconds):
            slept.append(seconds)

        monkeypatch.setattr(asyncio, 'sleep', no_sleep)
        result = asyncio.run(tl.get_device_metrics(device, 'dev1'))
        assert result == []
        assert device.calls == 3
        assert slept == [2, 4]
