"""Duevi CE-LAN device health sensors for Home Assistant.

Exposes physical device diagnostics (battery percentage, temperature, signal strength)
as Home Assistant sensor entities.

Device discovery is done centrally in __init__.py during config entry setup.
Live device status is polled via query 57 (READ_DEVICES_STAT) every 30 seconds.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_FAMILY_NAMES,
    DOMAIN,
    KEY_DEV_NET_QUALITY,
    KEY_DEV_POWER_SUPPLY,
    KEY_DEV_TEMPERATURE,
)
from .device_status import DueviDeviceCoordinator, battery_percentage, has_battery

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30.0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Duevi sensor platform from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    devices: dict = entry_data["devices"]

    if not devices:
        _LOGGER.warning("No physical devices found on Duevi alarm")
        return

    # Create the shared coordinator that manages device status polling
    coordinator = entry_data["device_coordinator"]

    entities: list[SensorEntity] = []
    for dev_idx, dev_cfg in devices.items():
        dev_name = dev_cfg.get("name", "").strip()
        if not dev_name:
            dev_name = f"Device {dev_idx}"

        # Use advertised capabilities rather than guessing from the family.
        if has_battery(dev_cfg):
            entities.append(DueviDeviceBatterySensor(coordinator, dev_idx, dev_cfg))
        if dev_cfg.get("transport") == 2:
            entities.append(DueviDeviceSignalSensor(coordinator, dev_idx, dev_cfg))

        # Temperature sensor (always add, entity handles None temperatures dynamically)
        entities.append(DueviDeviceTemperatureSensor(coordinator, dev_idx, dev_cfg))

    _LOGGER.info("Setting up %d Duevi device health sensor entities", len(entities))
    async_add_entities(entities, True)


class DueviBaseDeviceSensor(SensorEntity):
    """Base class for Duevi physical device health sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DueviDeviceCoordinator,
        device_index: int,
        device_cfg: dict[str, Any],
    ) -> None:
        self._coordinator = coordinator
        self._device_index = device_index
        self._device_cfg = device_cfg

        dev_name = device_cfg.get("name", "").strip() or f"Device {device_index}"
        family = device_cfg.get("family", -1)
        self._family_name = DEVICE_FAMILY_NAMES.get(family, f"Family {family}")

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"duevi_device_{device_index}")},
            "name": dev_name,
            "manufacturer": "Duevi",
            "model": self._family_name,
        }

    @property
    def available(self) -> bool:
        """Sensor is available as long as coordinator is healthy."""
        stat = self._get_my_stat()
        return self._coordinator.is_available and stat is not None and not stat.get("miss_dev")

    def _get_my_stat(self) -> dict[str, Any] | None:
        """Helper to find this device's stat entry from coordinator array."""
        return self._coordinator.get_cached_status(self._device_index)


class DueviDeviceBatterySensor(DueviBaseDeviceSensor):
    """Battery percentage sensor for a Duevi physical device."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: DueviDeviceCoordinator,
        device_index: int,
        device_cfg: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_index, device_cfg)
        dev_name = device_cfg.get("name", "").strip() or f"Device {device_index}"
        self._attr_name = "Battery"
        self._attr_unique_id = f"duevi_dev_{device_index}_battery"

    def update(self) -> None:
        """Fetch battery level from device stats."""
        self._coordinator.get_device_stats()
        stat = self._get_my_stat()
        if stat is not None:
            pct = battery_percentage(stat, self._device_cfg)
            self._attr_native_value = pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional details such as AC power supply state."""
        attrs: dict[str, Any] = {
            "device_index": self._device_index,
            "family": self._family_name,
        }
        stat = self._get_my_stat()
        if stat is not None:
            attrs["mains_powered"] = stat.get(KEY_DEV_POWER_SUPPLY, False)
            attrs["battery_raw"] = stat.get("battery_raw")
            attrs["packet_received_time_raw"] = stat.get("packet_received_time")
        return attrs


class DueviDeviceTemperatureSensor(DueviBaseDeviceSensor):
    """Temperature sensor for a Duevi physical device."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: DueviDeviceCoordinator,
        device_index: int,
        device_cfg: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_index, device_cfg)
        dev_name = device_cfg.get("name", "").strip() or f"Device {device_index}"
        self._attr_name = "Temperature"
        self._attr_unique_id = f"duevi_dev_{device_index}_temperature"

    def update(self) -> None:
        """Fetch temperature from device stats."""
        self._coordinator.get_device_stats()
        stat = self._get_my_stat()
        if stat is not None:
            temp = stat.get(KEY_DEV_TEMPERATURE)
            self._attr_native_value = temp


class DueviDeviceSignalSensor(DueviBaseDeviceSensor):
    """RF Signal quality sensor for a Duevi physical device."""

    _attr_icon = "mdi:signal"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: DueviDeviceCoordinator,
        device_index: int,
        device_cfg: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, device_index, device_cfg)
        dev_name = device_cfg.get("name", "").strip() or f"Device {device_index}"
        self._attr_name = "Signal Quality"
        self._attr_unique_id = f"duevi_dev_{device_index}_signal"

    def update(self) -> None:
        """Fetch network signal quality from device stats."""
        self._coordinator.get_device_stats()
        stat = self._get_my_stat()
        if stat is not None:
            quality = stat.get(KEY_DEV_NET_QUALITY)
            self._attr_native_value = round(quality * 100 / 63) if quality is not None and 0 <= quality <= 63 else None
