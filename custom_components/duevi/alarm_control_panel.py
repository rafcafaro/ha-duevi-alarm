"""Duevi CE-LAN alarm control panel for Home Assistant."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .nabto_udp import DueviClient

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=5.0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Duevi alarm platform from a config entry."""
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    async_add_entities([DueviAlarm(client, entry.data["host"])], True)


# Number of consecutive poll failures before marking the alarm unavailable.
_ALARM_FAILURE_THRESHOLD = 2


class DueviAlarm(AlarmControlPanelEntity):
    """Representation of the Duevi CE-LAN alarm."""

    _attr_name = "Duevi CE-LAN"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )
    _attr_code_format = None  # auth via config, no code needed in UI
    _attr_code_arm_required = False

    def __init__(self, client: DueviClient, host: str) -> None:
        self._client = client
        self._attr_unique_id = f"duevi_celan_{host.replace('.', '_')}"
        self._state = "disarmed"
        self._consecutive_failures: int = 0

    @property
    def available(self) -> bool:
        """Alarm is available when recent polls are succeeding."""
        return self._consecutive_failures < _ALARM_FAILURE_THRESHOLD

    @property
    def state(self) -> str:
        return self._state

    def update(self) -> None:
        """Poll alarm status via READ_AREAS_STAT.

        On communication failure the last-known state is preserved to
        prevent flickering in the HA history graph.  After several
        consecutive failures the entity is marked unavailable.
        """
        if not self._client._connected:
            try:
                if not self._client.connect():
                    self._consecutive_failures += 1
                    return
            except Exception as e:
                self._consecutive_failures += 1
                _LOGGER.error("Failed to connect to Duevi alarm: %s", e)
                return

        status = self._client.get_status()
        if status is None:
            self._consecutive_failures += 1
            _LOGGER.warning(
                "No status from alarm (failure %d/%d), reconnecting...",
                self._consecutive_failures, _ALARM_FAILURE_THRESHOLD,
            )
            # Reconnect immediately — once a Nabto session dies,
            # every subsequent poll will also fail until we reconnect.
            self._client.disconnect()
            return

        # Success — reset failure counter and update state
        self._consecutive_failures = 0
        # nabto_udp _sm_to_ha returns valid string states 
        # (disarmed, armed_away, arming, pending, triggered)
        self._state = status["state"]
        _LOGGER.debug(
            "Alarm: %s (sm=%d, InsState=0x%02x, SectAlarm=0x%02x)",
            status["state"], status["sm"], status["ins_state"], status["sect_alarm"],
        )

    def alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the alarm."""
        if self._client.disarm():
            self._state = "disarmed"
            time.sleep(SCAN_INTERVAL.total_seconds() * 0.3)  # Allow panel to process state change before next poll
        else:
            _LOGGER.error("Disarm command failed")

    def alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the alarm (all sectors)."""
        if self._client.arm():
            self._state = "arming"
            time.sleep(SCAN_INTERVAL.total_seconds() * 0.3)  # Allow panel to process state change before next poll
        else:
            _LOGGER.error("Arm away command failed")

    def alarm_arm_home(self, code: str | None = None) -> None:
        """Arm partial (sector 1 only — perimeter)."""
        if self._client.arm_partial(sectors=1):
            self._state = "arming"
            time.sleep(SCAN_INTERVAL.total_seconds() * 0.3)  # Allow panel to process state change before next poll
        else:
            _LOGGER.error("Arm home command failed")
