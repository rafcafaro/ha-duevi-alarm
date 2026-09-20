"""Shared thread-safe cache for query 57. Properties only read cached data."""
import logging
import re
import threading
import time

from .const import DOMAIN, KEY_DEV_BATTERY_PCT

_LOGGER = logging.getLogger(__name__)
DEVICE_POLL_INTERVAL = 30.0


def remove_legacy_low_battery_entities(registry, entry_id):
    """Remove only this entry's obsolete, integration-generated battery flags."""
    for entity in list(registry.entities.values()):
        if (
            entity.config_entry_id == entry_id
            and entity.platform == DOMAIN
            and entity.entity_id.startswith("binary_sensor.")
            and re.fullmatch(r"duevi_dev_[0-9]+_low_battery", entity.unique_id)
        ):
            registry.async_remove(entity.entity_id)


def has_battery(config):
    """Use the advertised battery capability; exclude the main board."""
    return bool(config.get("type", 0) & 0x20) and config.get("transport") != 1


def battery_percentage(status, config):
    """An unreported radio battery is unknown, not a confirmed empty battery."""
    percentage = status.get(KEY_DEV_BATTERY_PCT)
    if config.get("transport") == 2 and not status.get("packet_received_time") and percentage == 0:
        return None
    return percentage


class DueviDeviceCoordinator:
    """Poll once per interval across both platforms, including on failures."""

    def __init__(self, client, host):
        self._client = client
        self._host = host
        self._last_stats = None
        self._last_poll_time = None
        self._lock = threading.Lock()
        self.is_available = False

    def get_cached_status(self, index):
        data = self._last_stats
        return data[index] if data and 0 <= index < len(data) else None

    def get_device_stats(self):
        with self._lock:
            now = time.monotonic()
            if self._last_poll_time is not None and now - self._last_poll_time < DEVICE_POLL_INTERVAL:
                return self._last_stats
            try:
                with self._client._lock:
                    if not self._client._connected and not self._client.connect():
                        self.is_available = False
                        return self._last_stats
                    stats = self._client.read_devices_stat()
                self.is_available = bool(stats)
                if stats:
                    self._last_stats = stats
            except Exception:
                self.is_available = False
                _LOGGER.exception("Error reading Duevi device diagnostics")
            finally:
                self._last_poll_time = time.monotonic()
            return self._last_stats
