"""Share a three-frame, memory-only gallery and serialize image operations."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .video import DueviVideoProtocol, ImageRecord, MAX_IMAGES, VideoError

_LOGGER = logging.getLogger(__name__)
CAPTURE_TIMEOUT = 120
CAPTURE_TAKES = 2


@dataclass(frozen=True)
class Snapshot:
    record: ImageRecord
    frame: int
    jpeg: bytes
    updated: datetime


class DueviVideoCoordinator(DataUpdateCoordinator):
    """Fetch existing frames periodically; only an explicit button starts capture."""

    def __init__(self, hass, entry, client, devices):
        super().__init__(hass, _LOGGER, name="Duevi VIDEO-PIR", config_entry=entry,
                         update_interval=timedelta(seconds=60))
        self.protocol = DueviVideoProtocol(client)
        self.devices = devices
        self.data = {index: () for index in devices}
        self._operation_lock = asyncio.Lock()
        self._capture_task = None

    async def _call(self, method, *args):
        return await self.hass.async_add_executor_job(method, *args)

    async def _gallery(self, records):
        # Build the whole update before publishing it. Failure keeps the old
        # gallery intact; no partial JPEG or wrongly attributed frame is shown.
        result = {}
        for device in self.devices:
            cached = {(s.record, s.frame): s for s in self.data.get(device, ())}
            selected = []
            for index in range(len(records) - 1, -1, -1):
                record = records[index]
                if not record.is_video_pir or record.device_index != device:
                    continue
                for frame in range(record.frame_count - 1, -1, -1):
                    snapshot = cached.get((record, frame))
                    if snapshot is None:
                        jpeg = await self._call(self.protocol.read_frame, index, frame, record)
                        snapshot = Snapshot(record, frame, jpeg, dt_util.utcnow())
                    selected.append(snapshot)
                    if len(selected) == MAX_IMAGES:
                        break
                if len(selected) == MAX_IMAGES:
                    break
            result[device] = tuple(selected)
        return result

    async def _async_update_data(self):
        # Keep normal HA polling responsive while a requested capture completes.
        if self._operation_lock.locked():
            return self.data
        async with self._operation_lock:
            try:
                if await self._call(self.protocol.is_capturing):
                    return self.data
                records = await self._call(self.protocol.read_list)
                return await self._gallery(records)
            except (VideoError, OSError) as error:
                raise UpdateFailed(str(error)) from error

    async def async_capture(self, device):
        if self._operation_lock.locked():
            raise HomeAssistantError("An image operation is already in progress; try again shortly")
        async with self._operation_lock:
            self._capture_task = asyncio.current_task()
            try:
                for take in range(CAPTURE_TAKES):
                    try:
                        await self._capture_once(device)
                    except (VideoError, OSError) as error:
                        raise VideoError(f"Capture {take + 1} of {CAPTURE_TAKES} failed: {error}") from error
            except (VideoError, OSError) as error:
                raise HomeAssistantError(str(error)) from error
            finally:
                self._capture_task = None

    async def _capture_once(self, device):
        """Complete and download one take before another can start."""
        if await self._call(self.protocol.is_capturing):
            raise VideoError("Panel is already capturing images")
        before = await self._call(self.protocol.read_list)
        await self._call(self.protocol.request_capture, self.devices[device]['serial_log'])
        deadline = asyncio.get_running_loop().time() + CAPTURE_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            try:
                if await self._call(self.protocol.is_capturing):
                    continue
                records = await self._call(self.protocol.read_list)
            except (VideoError, OSError):
                # Radio acquisition can temporarily interrupt responses.
                # Existing alarm polling handles reconnects. Retry reads
                # within the deadline, never the capture request itself.
                continue
            if any(r.is_video_pir and r.device_index == device
                   and r.frame_count and r not in before for r in records):
                self.async_set_updated_data(await self._gallery(records))
                return
        raise VideoError("Capture timed out; check VIDEO-PIR radio reception and user video permissions")

    async def async_shutdown(self):
        if self._capture_task is not None:
            self._capture_task.cancel()
            await asyncio.gather(self._capture_task, return_exceptions=True)
        await super().async_shutdown()


def video_device_info(index, cfg):
    """Join the physical device already used by diagnostics."""
    return {"identifiers": {("duevi", f"duevi_device_{index}")},
            "name": cfg.get('name', '').strip() or f"Device {index}",
            "manufacturer": "Duevi", "model": "VIDEO-PIR"}
