"""Replay the cached VIDEO-PIR gallery as MJPEG, without acquiring images."""
from homeassistant.components.camera import Camera, async_get_still_stream
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .video_coordinator import CAPTURE_TAKES, video_device_info

FRAME_INTERVAL = 1.0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id].get('video_coordinator')
    if coordinator is not None:
        async_add_entities(DueviAnimationCamera(entry, coordinator, index)
                           for index in coordinator.devices)


class DueviAnimationCamera(CoordinatorEntity, Camera):
    """A replay of stored photos, not a live camera or a capture trigger."""

    _attr_has_entity_name = True
    _attr_name = 'Recent images animation'
    _attr_icon = 'mdi:animation-play'

    def __init__(self, entry, coordinator, index):
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._index = index
        self._removed = False
        self._attr_unique_id = f'{entry.entry_id}_video_{index}_animation'
        self._attr_device_info = video_device_info(index, coordinator.devices[index])

    @property
    def available(self):
        return bool(self.coordinator.data.get(self._index)) and not self._removed

    @property
    def extra_state_attributes(self):
        return {'playback': 'cached_images',
                'frame_count': min(CAPTURE_TAKES, len(self.coordinator.data.get(self._index, ()))),
                'frame_interval_seconds': FRAME_INTERVAL}

    async def async_camera_image(self, width=None, height=None):
        """Static previews show the newest already-downloaded JPEG."""
        images = self.coordinator.data.get(self._index, ())[:CAPTURE_TAKES]
        return images[0].jpeg if images and not self._removed else None

    async def handle_async_mjpeg_stream(self, request):
        """Give each viewer its own oldest-to-newest playback cursor."""
        cursor = 0
        generation = ()

        async def next_frame():
            nonlocal cursor, generation
            if self._removed or request.transport is None or request.transport.is_closing():
                return None
            images = self.coordinator.data.get(self._index, ())[:CAPTURE_TAKES]
            current = tuple((image.record, image.frame) for image in images)
            if current != generation:
                generation = current
                cursor = 0
            if not images:
                return None
            image = images[len(images) - 1 - cursor]
            cursor = (cursor + 1) % len(images)
            return image.jpeg

        return await async_get_still_stream(request, next_frame, 'image/jpeg', FRAME_INTERVAL)

    async def async_will_remove_from_hass(self):
        self._removed = True
        await super().async_will_remove_from_hass()
