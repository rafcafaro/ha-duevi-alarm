"""Duevi CE-LAN binary sensors for Home Assistant.

Exposes input zones (doors, windows, PIR motion sensors) as binary_sensor
entities.  Roller-shutter (tapparella) zones are excluded by default.

Zone discovery is done centrally in __init__.py during config entry setup.
This module consumes the pre-discovered zones and creates binary sensor entities.

Live status is polled via query 54 (READ_INPUTS_STAT) every 1.0 second
to reliably catch short-lived wireless PIR motion triggers.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_FAMILY_NAMES,
    DOMAIN,
    FAILURE_THRESHOLD,
    KEY_LINE_STATE,
    LINE_STATE_NORMAL,
    SENSOR_TECH_CONTACT,
    SENSOR_TECH_CONTACT_DUAL,
    SENSOR_TECH_CONTACT_TAMPER,
    SENSOR_TECH_PIR,
    SENSOR_TECH_REED,
)
from .nabto_udp import DueviClient

_LOGGER = logging.getLogger(__name__)

# Very fast polling to catch short-lived PIR motion triggers
SCAN_INTERVAL = timedelta(seconds=1.0)

# Map sensor technology to HA device class
SENSOR_TECH_TO_DEVICE_CLASS = {
    SENSOR_TECH_CONTACT: BinarySensorDeviceClass.WINDOW,
    SENSOR_TECH_CONTACT_TAMPER: BinarySensorDeviceClass.WINDOW,
    SENSOR_TECH_CONTACT_DUAL: BinarySensorDeviceClass.WINDOW,
    SENSOR_TECH_REED: BinarySensorDeviceClass.DOOR,
    SENSOR_TECH_PIR: BinarySensorDeviceClass.MOTION,
}

# Zone names containing these substrings get a specific device class override
NAME_CLASS_OVERRIDES = {
    "porta": BinarySensorDeviceClass.DOOR,
    "ingresso": BinarySensorDeviceClass.DOOR,
    "finestra": BinarySensorDeviceClass.WINDOW,
    "cameretta": BinarySensorDeviceClass.WINDOW,
    "bagno": BinarySensorDeviceClass.WINDOW,
    "matrimoniale": BinarySensorDeviceClass.WINDOW,
    "cucina": BinarySensorDeviceClass.WINDOW,
    "soggiorno": BinarySensorDeviceClass.WINDOW,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Duevi binary sensor platform from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: DueviClient = entry_data["client"]
    devices: dict = entry_data["devices"]
    zones: dict = entry_data["zones"]

    if not zones:
        _LOGGER.warning("No matching sensor zones found on Duevi alarm")
        return

    _LOGGER.info("Setting up %d Duevi binary sensor entities", len(zones))

    # Create the shared coordinator that manages polling
    coordinator = DueviSensorCoordinator(client, entry.data["host"])

    # Create binary sensor entities
    entities = []
    for idx, zone_cfg in zones.items():
        dev_info = devices.get(zone_cfg["hw_dev_index"])
        entities.append(DueviBinarySensor(coordinator, idx, zone_cfg, dev_info))

    async_add_entities(entities, True)





class DueviSensorCoordinator:
    """Manages the DueviClient connection and rapid polling for sensors.

    Because Home Assistant polls each entity individually, this coordinator
    ensures we only query the panel once per 1.0s interval.

    Resilience strategy:
    - On a failed poll, the last-known-good stats are preserved so sensors
      never flicker to an incorrect state due to a transient UDP loss.
    - After _FAILURE_THRESHOLD consecutive failures the coordinator reports
      itself as unavailable so HA can show an "unavailable" badge instead of
      silently serving stale data.
    """

    def __init__(self, client: DueviClient, host: str) -> None:
        self._client = client
        self._host = host
        self._last_stats: list[dict[str, int]] | None = None
        self._last_poll_time: float = 0.0
        self._poll_lock = False  # simple reentrance guard (HA is single-threaded)
        self._consecutive_failures: int = 0

    @property
    def is_available(self) -> bool:
        """True when recent polls are succeeding."""
        return self._consecutive_failures < FAILURE_THRESHOLD

    def get_input_stats(self) -> list[dict[str, int]] | None:
        """Get the latest input stats, polling the panel if needed.

        Always returns the last-known-good stats on failure so that callers
        never see a spurious state reset.
        """
        now = time.time()

        # Deduplicate polls within a 0.8s window (entities sharing a 1.0s cycle)
        if now - self._last_poll_time < 0.8 and self._last_stats is not None:
            return self._last_stats

        if self._poll_lock:
            return self._last_stats
        self._poll_lock = True

        try:
            if not self._client._connected:
                self._client.connect()

            stats = self._client.read_inputs_stat()

            if stats:
                self._last_stats = stats
                self._last_poll_time = now
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                _LOGGER.debug(
                    "No input stats response (failure %d/%d), forcing reconnect",
                    self._consecutive_failures, FAILURE_THRESHOLD,
                )
                # Reconnect immediately — once a Nabto session dies,
                # every subsequent poll will also fail until we reconnect.
                self._client.disconnect()
                time.sleep(0.5)
                self._client.connect()

            return self._last_stats
        except Exception:
            self._consecutive_failures += 1
            _LOGGER.exception(
                "Error polling Duevi sensor stats (failure %d/%d)",
                self._consecutive_failures, _FAILURE_THRESHOLD,
            )
            self._client.disconnect()
            return self._last_stats
        finally:
            self._poll_lock = False


class DueviBinarySensor(BinarySensorEntity):
    """A single Duevi input zone as a Home Assistant binary sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DueviSensorCoordinator,
        zone_index: int,
        zone_cfg: dict[str, Any],
        device_info: dict[str, Any] | None,
    ) -> None:
        self._coordinator = coordinator
        self._zone_index = zone_index
        self._zone_cfg = zone_cfg
        self._device_info = device_info
        self._line_state: int = LINE_STATE_NORMAL

        # Entity attributes
        zone_name = zone_cfg["name"].strip()
        self._attr_name = zone_name
        self._attr_unique_id = f"duevi_zone_{zone_index}"

        # Determine device class from zone name or technology
        self._attr_device_class = self._infer_device_class(zone_name, zone_cfg["technology"])

    def _infer_device_class(
        self, name: str, technology: int
    ) -> BinarySensorDeviceClass:
        """Determine the HA device class from zone name and technology."""
        name_lower = name.lower()

        # Name-based overrides take priority
        for keyword, dev_class in NAME_CLASS_OVERRIDES.items():
            if keyword in name_lower:
                return dev_class

        # Fall back to technology-based mapping
        return SENSOR_TECH_TO_DEVICE_CLASS.get(technology, BinarySensorDeviceClass.WINDOW)

    @property
    def available(self) -> bool:
        """Sensor is available as long as the coordinator is receiving data."""
        return self._coordinator.is_available

    @property
    def is_on(self) -> bool:
        """Return True if the sensor is triggered (open/motion/alarm)."""
        return self._line_state != LINE_STATE_NORMAL

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for debugging and transparency."""
        attrs: dict[str, Any] = {
            "zone_index": self._zone_index,
            "line_state": self._line_state,
            "technology": self._zone_cfg.get("technology", -1),
        }
        if self._device_info:
            attrs["device_name"] = self._device_info.get("name", "unknown")
            family = self._device_info.get("family", -1)
            attrs["device_family"] = DEVICE_FAMILY_NAMES.get(family, str(family))
        return attrs

    def update(self) -> None:
        """Poll the sensor status via the shared coordinator.

        State is only updated when we receive a confirmed good reading.
        On communication failure the last-known state is preserved to
        prevent flickering in the HA history graph.
        """
        stats = self._coordinator.get_input_stats()
        if stats is None:
            # Communication failure — hold previous state, availability
            # is tracked by the coordinator.
            return
        if self._zone_index < len(stats):
            self._line_state = stats[self._zone_index][KEY_LINE_STATE]
