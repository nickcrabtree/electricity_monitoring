"""Optional flint MQTT bridge; all device access stays on the poller's asyncio loop."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from pathlib import Path

import paho.mqtt.client as mqtt

import config
from device_names import get_device_name
from graphite_helper import format_device_name

logger = logging.getLogger(__name__)
_DEVICE_ERRORS = (asyncio.TimeoutError, OSError, ValueError, TypeError, AttributeError, KeyError)


def topic(device_id, suffix):
    return f'flint/{device_id}/{suffix}'


def command_value(message_topic, payload, retained, devices):
    """Validate without decoding arbitrary input or accepting retained commands."""
    parts = message_topic.split('/')
    if retained or len(parts) != 3 or parts[0] != 'flint' or parts[2] != 'set':
        return None
    if parts[1] not in devices or payload not in (b'ON', b'OFF'):
        return None
    return parts[1], payload == b'ON'


def discovery_payloads(device_id, name, has_power):
    slug = format_device_name(name)
    common = {
        'availability': [{'topic': 'flint/status'}, {'topic': topic(device_id, 'availability')}],
        'availability_mode': 'all',
        'device': {'identifiers': [f'tuya_{device_id}'], 'name': name, 'via_device': 'flint'},
    }
    result = {
        f'homeassistant/switch/flint_{device_id}/config': {
            **common,
            'name': None,
            'unique_id': f'flint_{device_id}',
            'default_entity_id': f'switch.{slug}_local',
            'command_topic': topic(device_id, 'set'),
            'state_topic': topic(device_id, 'state'),
            'payload_on': 'ON',
            'payload_off': 'OFF',
            'retain': False,
        }
    }
    if has_power:
        result[f'homeassistant/sensor/flint_{device_id}_power/config'] = {
            **common,
            'name': 'Power',
            'unique_id': f'flint_{device_id}_power',
            'default_entity_id': f'sensor.{slug}_local_power',
            'state_topic': topic(device_id, 'power'),
            'device_class': 'power',
            'unit_of_measurement': 'W',
            'state_class': 'measurement',
        }
    return result


async def device_call(function, *args, timeout=5, **kwargs):
    """A timeout must not release the caller's lock while its thread still runs."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Tuya socket retries are finite; cancellation cannot stop a running thread.
        await asyncio.gather(task, return_exceptions=True)
        raise


class MQTTBridge:
    def __init__(self, client, loop, name_for=get_device_name):
        self.client = client
        self.loop = loop
        self.name_for = name_for
        self.devices = {}
        self.locks = {}
        self.values = {}
        self.available = {}
        self.tasks = set()
        self.outage = False
        self.stopping = False

    def start(self):
        self.client.will_set('flint/status', 'offline', retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        # DNS and connection attempts occur only in paho's background thread.
        self.client.connect_async(config.MQTT_BROKER_HOST, config.MQTT_BROKER_PORT)
        self.client.loop_start()

    def _dispatch(self, callback, *args):
        if not self.stopping and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(callback, *args)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self._dispatch(self.disconnected if reason_code.is_failure else self.connected)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._dispatch(self.disconnected)

    def _on_connect_fail(self, client, userdata):
        self._dispatch(self.disconnected)

    def _on_message(self, client, userdata, message):
        self._dispatch(self._queue_command, message.topic, message.payload, message.retain)

    def _queue_command(self, *args):
        if self.stopping:
            return
        task = asyncio.create_task(self.command(*args))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def publish(self, message_topic, payload):
        self.client.publish(message_topic, payload, retain=True)

    def disconnected(self):
        if not self.outage:
            logger.warning('MQTT bridge unavailable; retrying in background')
            self.outage = True

    def connected(self):
        if self.stopping:
            return
        self.outage = False
        self.client.subscribe('flint/+/set')
        self.publish(
            'homeassistant/binary_sensor/flint_bridge/config',
            json.dumps(
                {
                    'name': 'Connectivity',
                    'unique_id': 'flint_bridge',
                    'device_class': 'connectivity',
                    'state_topic': 'flint/status',
                    'payload_on': 'online',
                    'payload_off': 'offline',
                    'device': {'identifiers': ['flint'], 'name': 'flint'},
                }
            ),
        )
        for device_id in self.devices:
            self.discover(device_id)
            self.publish(topic(device_id, 'availability'), 'online' if self.available.get(device_id) else 'offline')
            for key, value in self.values.get(device_id, {}).items():
                self.publish(topic(device_id, key), str(value))
        self.publish('flint/status', 'online')

    def update_devices(self, devices):
        for device_id in self.devices.keys() - devices.keys():
            self.publish(topic(device_id, 'availability'), 'offline')
            self.available.pop(device_id, None)
            self.values.pop(device_id, None)
        self.devices = dict(devices)
        # Keep locks across rescans, including removal/reappearance during a command.
        for device_id in devices:
            self.locks.setdefault(device_id, asyncio.Lock())
            self.discover(device_id)

    def discover(self, device_id):
        values = self.values.get(device_id, {})
        if 'state' not in values:
            return
        for message_topic, payload in discovery_payloads(
            device_id, self.name_for(device_id), 'power' in values
        ).items():
            self.publish(message_topic, json.dumps(payload))

    def poll_result(self, device_id, metrics, success):
        self.available[device_id] = success
        self.publish(topic(device_id, 'availability'), 'online' if success else 'offline')
        if not success:
            return
        values = self.values.setdefault(device_id, {})
        previous_keys = set(values)
        for metric, value in metrics:
            if metric.endswith('.is_on'):
                state = 'ON' if value else 'OFF'
                if values.get('state') != state:
                    self.publish(topic(device_id, 'state'), state)
                values['state'] = state
            elif metric.endswith('.power_watts'):
                values['power'] = value
                self.publish(topic(device_id, 'power'), str(value))
        if set(values) != previous_keys:
            self.discover(device_id)

    async def command(self, message_topic, payload, retained):
        if retained:
            logger.warning('Ignoring retained MQTT command')
        validated = command_value(message_topic, payload, retained, self.devices)
        if validated is None:
            return
        device_id, value = validated
        async with self.locks[device_id]:
            # The device list may have changed while waiting for the lock.
            device = self.devices.get(device_id)
            if device is None or self.stopping:
                return
            result = 'failed'
            try:
                response = await device_call(device.set_status, value, switch=1)
                result = 'sent' if not isinstance(response, dict) or 'Error' not in response else 'failed'
            except _DEVICE_ERRORS:
                logger.warning('MQTT switch command failed for %s', self.name_for(device_id))
            try:
                status = await device_call(device.status)
                dps = status.get('dps', {}) if isinstance(status, dict) else {}
                if '1' in dps:
                    state = 'ON' if dps['1'] else 'OFF'
                    self.values.setdefault(device_id, {})['state'] = state
                    self.publish(topic(device_id, 'state'), state)
                    result += f'; read-back {state}'
                else:
                    result += '; read-back unavailable'
            except _DEVICE_ERRORS:
                result += '; read-back unavailable'
            logger.info('MQTT %s command %s: %s', self.name_for(device_id), 'ON' if value else 'OFF', result)

    async def stop(self):
        self.stopping = True
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.publish('flint/status', 'offline')
        self.client.disconnect()
        await asyncio.to_thread(self.client.loop_stop)


def create_bridge(path=None):
    if socket.gethostname() not in config.MQTT_BRIDGE_HOSTS:
        return None
    path = Path(path) if path is not None else Path(__file__).with_name('mqtt_local.json')
    if not path.exists():
        return None
    try:
        credentials = json.loads(path.read_text())
        username, password = credentials['username'], credentials['password']
        if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
            raise ValueError('Invalid credentials')
    except (OSError, ValueError, KeyError, TypeError):
        logger.error('MQTT credentials unreadable or invalid; bridge disabled')
        return None
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(username, password)
    return MQTTBridge(client, asyncio.get_running_loop())
