"""Expose the last three VIDEO-PIR JPEG frames through HA's image proxy."""
from homeassistant.components.image import ImageEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .video import MAX_IMAGES
from .video_coordinator import video_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id].get('video_coordinator')
    if coordinator is not None:
        async_add_entities(DueviImage(hass, entry, coordinator, index, slot)
                           for index in coordinator.devices for slot in range(MAX_IMAGES))


class DueviImage(CoordinatorEntity, ImageEntity):
    _attr_has_entity_name = True
    _attr_content_type = 'image/jpeg'

    def __init__(self, hass, entry, coordinator, index, slot):
        ImageEntity.__init__(self, hass)
        CoordinatorEntity.__init__(self, coordinator)
        self._index = index
        self._slot = slot
        self._attr_name = 'Latest image' if slot == 0 else f'Previous image {slot}'
        self._attr_unique_id = f'{entry.entry_id}_video_{index}_image_{slot}'
        self._attr_device_info = video_device_info(index, coordinator.devices[index])

    @property
    def snapshot(self):
        images = self.coordinator.data.get(self._index, ())
        return images[self._slot] if self._slot < len(images) else None

    @property
    def available(self):
        # A cached photo remains viewable if the panel temporarily goes offline.
        return self.snapshot is not None

    @property
    def image_last_updated(self):
        return self.snapshot.updated if self.snapshot else None

    async def async_image(self):
        return self.snapshot.jpeg if self.snapshot else None
