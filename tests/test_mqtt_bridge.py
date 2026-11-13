import asyncio
import json
import logging
import selectors
from unittest.mock import Mock

import pytest

from mqtt_bridge import MQTTBridge, create_bridge, discovery_payloads


@pytest.fixture(autouse=True)
def bounded_selector_wait(monkeypatch):
    # Restricted sandboxes can drop asyncio's cross-thread socket wakeup. Keep
    # real thread/lock tests (including executor shutdown) progressing there.
    select = selectors.DefaultSelector.select

    def bounded(self, timeout=None):
        return select(self, 0.01 if timeout is None else min(timeout, 0.01))

    monkeypatch.setattr(selectors.DefaultSelector, 'select', bounded)


def bridge():
    client = Mock()
    result = MQTTBridge(client, asyncio.get_running_loop(), lambda _: 'Towel Rail')
    device = Mock()
    device.status.return_value = {'dps': {'1': False}}
    result.update_devices({'abc': device})
    return result, client, device


@pytest.mark.parametrize(
    'topic,payload,retain',
    [
        ('flint/abc/set', b'ON', True),
        ('flint/abc/set', b'on', False),
        ('flint/abc/set', b'ON\n', False),
        ('flint/unknown/set', b'OFF', False),
        ('other/abc/set', b'ON', False),
    ],
)
def test_invalid_commands(topic, payload, retain, caplog):
    async def run():
        b, _, dev = bridge()
        await b.command(topic, payload, retain)
        dev.set_status.assert_not_called()

    asyncio.run(run())
    if retain:
        assert 'retained' in caplog.text


def test_command_lock_and_real_readback():
    async def run():
        b, client, dev = bridge()
        dev.set_status.side_effect = lambda *a, **k: b.locks['abc'].locked() or pytest.fail('unlocked')
        async with b.locks['abc']:
            task = asyncio.create_task(b.command('flint/abc/set', b'ON', False))
            await asyncio.sleep(0)
            dev.set_status.assert_not_called()
        await task
        dev.set_status.assert_called_once_with(True, switch=1)
        client.publish.assert_any_call('flint/abc/state', 'OFF', retain=True)

    asyncio.run(run())


def test_discovery():
    configs = discovery_payloads('abc', 'Towel Rail', True)
    switch = configs['homeassistant/switch/flint_abc/config']
    assert switch['name'] is None
    assert switch['unique_id'] == 'flint_abc'
    assert switch['default_entity_id'] == 'switch.towel_rail_local'
    assert 'object_id' not in switch
    assert switch['retain'] is False
    assert switch['availability_mode'] == 'all'
    assert switch['availability'] == [{'topic': 'flint/status'}, {'topic': 'flint/abc/availability'}]
    assert switch['device'] == {'identifiers': ['tuya_abc'], 'name': 'Towel Rail', 'via_device': 'flint'}
    power = configs['homeassistant/sensor/flint_abc_power/config']
    assert power['default_entity_id'] == 'sensor.towel_rail_local_power'
    assert power['unit_of_measurement'] == 'W'
    assert power['device_class'] == 'power'
    assert power['state_class'] == 'measurement'
    assert len(discovery_payloads('abc', 'Towel Rail', False)) == 1


def test_lifecycle_and_poll_state():
    async def run():
        b, client, dev = bridge()
        b.start()
        client.will_set.assert_called_once_with('flint/status', 'offline', retain=True)
        client.connect_async.assert_called_once()
        b.poll_result('abc', [('x.is_on', 1), ('x.power_watts', 12.3)], True)
        b.connected()
        b.connected()
        client.subscribe.assert_called_with('flint/+/set')
        client.publish.assert_any_call('flint/status', 'online', retain=True)
        client.publish.assert_any_call('flint/abc/state', 'ON', retain=True)
        client.publish.assert_any_call('flint/abc/power', '12.3', retain=True)
        b.poll_result('abc', [], False)
        client.publish.assert_any_call('flint/abc/availability', 'offline', retain=True)
        dev.set_status.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize('hostname,exists', [('flint', False), ('blackpi2', True)])
def test_disabled(tmp_path, monkeypatch, hostname, exists):
    path = tmp_path / 'credentials.json'
    if exists:
        path.write_text(json.dumps({'username': 'test', 'password': 'secret'}))
    monkeypatch.setattr('mqtt_bridge.socket.gethostname', lambda: hostname)
    assert create_bridge(path=path) is None


def test_credentials_never_logged(tmp_path, monkeypatch, caplog):
    path = tmp_path / 'credentials.json'
    path.write_text('{"password": "super-secret", invalid}')
    monkeypatch.setattr('mqtt_bridge.socket.gethostname', lambda: 'flint')
    with caplog.at_level(logging.DEBUG):
        assert create_bridge(path=path) is None
    assert 'super-secret' not in caplog.text


def test_failure_reads_real_state(caplog):
    async def run():
        b, client, dev = bridge()
        dev.set_status.side_effect = OSError('private data')
        await b.command('flint/abc/set', b'ON', False)
        client.publish.assert_any_call('flint/abc/state', 'OFF', retain=True)
        assert 'private data' not in caplog.text

    asyncio.run(run())


def test_timeout_keeps_lock_until_thread_finishes():
    import threading

    from mqtt_bridge import device_call

    async def run():
        finished = threading.Event()
        release = threading.Event()

        def slow():
            release.wait(1)
            finished.set()

        lock = asyncio.Lock()

        async def operation():
            async with lock:
                await device_call(slow, timeout=0.01)

        task = asyncio.create_task(operation())
        await asyncio.sleep(0.04)
        assert lock.locked()
        release.set()
        with pytest.raises(TimeoutError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_enabled_factory_uses_v2_and_private_credentials(tmp_path, monkeypatch, caplog):
    import paho.mqtt.client as mqtt

    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'username': 'test', 'password': 'super-secret'}))
    factory = Mock()
    monkeypatch.setattr('mqtt_bridge.socket.gethostname', lambda: 'flint')
    monkeypatch.setattr('mqtt_bridge.mqtt.Client', factory)

    async def run():
        b = create_bridge(path)
        factory.assert_called_once_with(mqtt.CallbackAPIVersion.VERSION2)
        b.client.username_pw_set.assert_called_once_with('test', 'super-secret')

    asyncio.run(run())
    assert 'super-secret' not in caplog.text


def test_outage_warns_once_until_reconnect(caplog):
    async def run():
        b, _, _ = bridge()
        b.disconnected()
        b.disconnected()
        assert caplog.text.count('retrying in background') == 1
        b.connected()
        b.disconnected()
        assert caplog.text.count('retrying in background') == 2

    asyncio.run(run())


def test_rescan_and_state_change_publication():
    async def run():
        b, client, dev = bridge()
        b.poll_result('abc', [('x.is_on', 0)], True)
        client.reset_mock()
        b.poll_result('abc', [('x.is_on', 0)], True)
        assert all(call.args[0] != 'flint/abc/state' for call in client.publish.call_args_list)
        b.update_devices({'abc': dev, 'new': Mock()})
        b.poll_result('new', [('x.is_on', 1)], True)
        configs = [call.args[0] for call in client.publish.call_args_list]
        assert 'homeassistant/switch/flint_new/config' in configs
        assert 'homeassistant/sensor/flint_new_power/config' not in configs
        b.update_devices({})
        await b.command('flint/abc/set', b'ON', False)
        dev.set_status.assert_not_called()
        client.publish.assert_any_call('flint/abc/availability', 'offline', retain=True)

    asyncio.run(run())


def test_poller_uses_same_lock_and_scaled_power(monkeypatch):
    import tuya_local_to_graphite as poller

    async def run():
        b, client, dev = bridge()

        def status():
            assert b.locks['abc'].locked()
            return {'dps': {'1': True, '19': 123}}

        dev.status.side_effect = status
        monkeypatch.setattr(poller, '_mark_local_success', Mock())
        monkeypatch.setattr(poller, 'get_device_name', lambda _: 'Towel Rail')
        monkeypatch.setattr(poller, '_metric_scaler', Mock(normalize_by_dps=Mock(return_value=12.3)))
        sender = Mock(return_value=2)
        monkeypatch.setattr(poller, 'send_metrics', sender)
        assert await poller.poll_devices_once({'abc': dev}, bridge=b) == 2
        client.publish.assert_any_call('flint/abc/power', '12.3', retain=True)
        assert ('home.electricity.tuya.towel_rail.power_watts', 12.3) in sender.call_args.args[2]

    asyncio.run(run())


def test_network_callback_hands_command_to_loop():
    import threading

    async def run():
        b, _, dev = bridge()
        message = Mock(topic='flint/abc/set', payload=b'OFF', retain=False)
        thread = threading.Thread(target=b._on_message, args=(None, None, message))
        thread.start()
        thread.join()
        await asyncio.sleep(0)
        await asyncio.gather(*b.tasks)
        dev.set_status.assert_called_once_with(False, switch=1)

    asyncio.run(run())
