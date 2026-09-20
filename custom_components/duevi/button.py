"""User-triggered VIDEO-PIR capture; viewing images never triggers a capture."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .video_coordinator import video_device_info


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id].get('video_coordinator')
    if coordinator is not None:
        async_add_entities(DueviCaptureButton(entry, coordinator, index)
                           for index in coordinator.devices)


class DueviCaptureButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = 'Capture 2 images'
    _attr_icon = 'mdi:camera-plus'

    def __init__(self, entry, coordinator, index):
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f'{entry.entry_id}_video_{index}_capture'
        self._attr_device_info = video_device_info(index, coordinator.devices[index])

    async def async_press(self):
        await self.coordinator.async_capture(self._index)
