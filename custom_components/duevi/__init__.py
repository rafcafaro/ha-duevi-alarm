"""Duevi CE-LAN alarm integration for Home Assistant.

This integration communicates directly with the Duevi CE-LAN alarm panel
over local UDP (Nabto Micro protocol) — no cloud relay required.

Both alarm_control_panel and binary_sensor platforms share a SINGLE
DueviClient instance to avoid session conflicts (the panel only supports
one active session at a time).

Setup flow:
1. __init__.py connects the client and runs zone discovery ONCE
2. Discovered zones are stored in hass.data for binary_sensor.py
3. Only then are platforms forwarded — both use the already-connected client
"""
from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PIN,
    DOMAIN,
    INCLUDED_SENSOR_TECHS,
)
from .nabto_udp import DueviClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR]




def _connect_and_discover(client: DueviClient) -> tuple[dict, dict]:
    """Connect to the panel and discover devices + input zones.

    Returns (devices_dict, zones_dict).  Both may be empty on failure.
    """
    if not client.connect():
        _LOGGER.error("Cannot connect to Duevi alarm for setup")
        return {}, {}

    # 1. Discover physical devices (query 56 × 8)
    devices: dict[int, dict] = {}
    for i in range(8):
        dev = client.read_device_cfg(i)
        if dev:
            devices[i] = dev
            _LOGGER.debug("Device %d: %s (family=%d)", i, dev["name"], dev["family"])
        time.sleep(0.1)

    # 2. Discover input zones (query 53 × 20)
    zones: dict[int, dict] = {}
    for i in range(20):
        cfg = client.read_input_cfg(i)
        if cfg and cfg["name"].strip():
            tech = cfg["technology"]
            if tech in INCLUDED_SENSOR_TECHS:
                zones[i] = cfg
                dev_name = devices.get(cfg["hw_dev_index"], {}).get("name", "?")
                _LOGGER.debug(
                    "Zone %d: %s (tech=%d, device=%s)", i, cfg["name"], tech, dev_name
                )
        time.sleep(0.1)

    _LOGGER.info(
        "Duevi discovery complete: %d devices, %d sensor zones", len(devices), len(zones)
    )
    return devices, zones


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Duevi CE-LAN Alarm from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    email = entry.data[CONF_EMAIL]
    pin = entry.data[CONF_PIN]
    port = entry.data.get(CONF_PORT, 5570)

    client = DueviClient(host=host, email=email, pin=pin, port=port)

    # Connect and discover zones BEFORE forwarding to platforms.
    # This avoids race conditions between alarm_control_panel and
    # binary_sensor both trying to connect the same client concurrently.
    devices, zones = await hass.async_add_executor_job(
        _connect_and_discover, client
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "devices": devices,
        "zones": zones,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        client: DueviClient = entry_data["client"]
        await hass.async_add_executor_job(client.disconnect)

    return unload_ok
