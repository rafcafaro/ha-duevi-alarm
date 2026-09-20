"""Offline protocol and concurrent cache regression tests. No panel access."""
import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import struct
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

package = types.ModuleType('duevi_test')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/duevi')]
sys.modules[package.__name__] = package
wire = importlib.import_module('duevi_test.nabto_udp')
health = importlib.import_module('duevi_test.device_status')


def record(battery=100, received=123, temperature=126):
    return struct.pack('>7BIb', 1, 2, 0, 63, 4, 5, battery, received, temperature)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = wire.DueviClient('unused', 'unused', 'unused')
        self.client._send_sensor_query = Mock()

    def test_complete_records_and_power_bit(self):
        self.client._send_sensor_query.return_value = b'\x00\x02' + record(212, 0x01020304, -12) + record(14)
        stats = self.client.read_devices_stat()
        self.client._send_sensor_query.assert_called_once_with(57, 0)
        self.assertEqual(stats[0]['battery_pct'], 84)
        self.assertTrue(stats[0]['power_supply'])
        self.assertEqual(stats[0]['temperature'], 19)
        self.assertEqual(stats[0]['packet_received_time'], 0x01020304)
        self.assertEqual(stats[1]['battery_pct'], 14)
        self.assertIsNone(stats[1]['temperature'])

    def test_rejects_partial_responses(self):
        for response in [b'', b'\x00', b'\x00\x00', b'\x00\x02' + record(), b'\x00\x01' + record()[:-1], b'\x00\x01' + record() + b'\x00']:
            with self.subTest(response=response):
                self.client._send_sensor_query.return_value = response
                self.assertIsNone(self.client.read_devices_stat())

    def test_percentage_boundaries(self):
        for raw, expected in [(0, 0), (100, 100), (128, 0), (228, 100), (101, None), (255, None)]:
            self.client._send_sensor_query.return_value = b'\x00\x01' + record(raw)
            self.assertEqual(self.client.read_devices_stat()[0]['battery_pct'], expected)

    def test_transport_and_capabilities(self):
        for transport in [0, 1, 2, 7]:
            self.client._send_sensor_query.return_value = b'\x00\x04Door' + struct.pack('>IBBBBBBH', 123, transport, 0, 34, 75, 2, 0, 256)
            cfg = self.client.read_device_cfg(3)
            if transport == 0:
                self.assertIsNone(cfg)
            else:
                self.assertEqual(cfg['transport'], transport)
                self.assertEqual(cfg['type'], 34)
                self.assertEqual(health.has_battery(cfg), transport != 1)
        self.assertFalse(health.has_battery({'type': 70, 'transport': 3}))

    def test_unreported_radio_battery_is_unknown(self):
        cfg = {'transport': 2}
        self.assertIsNone(health.battery_percentage({'battery_pct': 0, 'packet_received_time': 0}, cfg))
        self.assertEqual(health.battery_percentage({'battery_pct': 0, 'packet_received_time': 123}, cfg), 0)
        self.assertEqual(health.battery_percentage({'battery_pct': 100, 'packet_received_time': 0}, cfg), 100)


class RegistryCleanupTests(unittest.TestCase):
    def test_only_obsolete_flags_of_current_entry_are_removed(self):
        def entity(entity_id, unique_id, entry_id='current', platform='duevi'):
            return types.SimpleNamespace(entity_id=entity_id, unique_id=unique_id,
                                         config_entry_id=entry_id, platform=platform)
        entries = [
            entity('binary_sensor.renamed_warning', 'duevi_dev_6_low_battery'),
            entity('sensor.ingresso_battery', 'duevi_dev_6_battery'),
            entity('binary_sensor.other_entry', 'duevi_dev_6_low_battery', entry_id='other'),
            entity('binary_sensor.other_integration', 'duevi_dev_6_low_battery', platform='mqtt'),
            entity('binary_sensor.tamper', 'duevi_dev_6_tamper'),
            entity('binary_sensor.custom', 'custom_low_battery'),
        ]
        registry = Mock(entities={e.entity_id: e for e in entries})
        registry.async_remove.side_effect = registry.entities.pop
        health.remove_legacy_low_battery_entities(registry, 'current')
        registry.async_remove.assert_called_once_with('binary_sensor.renamed_warning')
        self.assertEqual(len(registry.entities), 5)
        health.remove_legacy_low_battery_entities(registry, 'current')
        self.assertEqual(registry.async_remove.call_count, 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(_lock=threading.RLock(), _connected=True)
        self.client.read_devices_stat.return_value = [{'battery_pct': 84}]
        self.cache = health.DueviDeviceCoordinator(self.client, 'unused')

    def test_concurrent_entities_share_one_query(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.cache.get_device_stats(), range(24)))
        self.client.read_devices_stat.assert_called_once()
        self.assertTrue(self.cache.is_available)
        self.assertTrue(all(item == results[0] for item in results))

    def test_cached_properties_never_poll(self):
        self.assertIsNone(self.cache.get_cached_status(0))
        self.client.read_devices_stat.assert_not_called()
        self.cache.get_device_stats()
        self.assertEqual(self.cache.get_cached_status(0)['battery_pct'], 84)
        self.assertIsNone(self.cache.get_cached_status(99))
        self.client.read_devices_stat.assert_called_once()

    def test_failure_throttling_and_recovery(self):
        with patch.object(health.time, 'monotonic', return_value=100):
            self.cache.get_device_stats()
        self.client.read_devices_stat.return_value = None
        with patch.object(health.time, 'monotonic', return_value=131):
            for _ in range(20):
                self.assertEqual(self.cache.get_device_stats(), [{'battery_pct': 84}])
        self.assertFalse(self.cache.is_available)
        self.assertEqual(self.client.read_devices_stat.call_count, 2)
        self.client.read_devices_stat.return_value = [{'battery_pct': 70}]
        with patch.object(health.time, 'monotonic', return_value=162):
            self.assertEqual(self.cache.get_device_stats(), [{'battery_pct': 70}])
        self.assertTrue(self.cache.is_available)


if __name__ == '__main__':
    unittest.main()
