"""Synthetic protocol and gallery regressions; no credentials or panel access."""
import asyncio
import base64
from datetime import datetime, timezone
import importlib
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

package = types.ModuleType('duevi_video_test')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/duevi')]
sys.modules[package.__name__] = package
video = importlib.import_module('duevi_video_test.video')
wire = importlib.import_module('duevi_video_test.nabto_udp')


class FakeCoordinator:
    def __init__(self, hass, *args, **kwargs):
        self.hass = hass

    def async_set_updated_data(self, data):
        self.data = data

    async def async_shutdown(self):
        pass


class FakeEntity:
    pass


class FakeCoordinatorEntity(FakeEntity):
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_will_remove_from_hass(self):
        pass


class FakeImageEntity(FakeEntity):
    def __init__(self, hass):
        self.hass = hass


class FakeCamera(FakeEntity):
    pass


class HAError(Exception):
    pass


stubs = {}
for name in ('homeassistant', 'homeassistant.exceptions', 'homeassistant.helpers',
             'homeassistant.helpers.update_coordinator', 'homeassistant.util',
             'homeassistant.util.dt', 'homeassistant.components',
             'homeassistant.components.image', 'homeassistant.components.button',
             'homeassistant.components.camera'):
    stubs[name] = types.ModuleType(name)
stubs['homeassistant.exceptions'].HomeAssistantError = HAError
stubs['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator = FakeCoordinator
stubs['homeassistant.helpers.update_coordinator'].UpdateFailed = HAError
stubs['homeassistant.helpers.update_coordinator'].CoordinatorEntity = FakeCoordinatorEntity
stubs['homeassistant.components.image'].ImageEntity = FakeImageEntity
stubs['homeassistant.components.button'].ButtonEntity = FakeEntity
stubs['homeassistant.components.camera'].Camera = FakeCamera
stubs['homeassistant.components.camera'].async_get_still_stream = AsyncMock()
stubs['homeassistant.util.dt'].utcnow = lambda: datetime.now(timezone.utc)
with patch.dict(sys.modules, stubs):
    coordinator_module = importlib.import_module('duevi_video_test.video_coordinator')
    image_module = importlib.import_module('duevi_video_test.image')
    button_module = importlib.import_module('duevi_video_test.button')
    camera_module = importlib.import_module('duevi_video_test.camera')

JPEG = b'\xff\xd8synthetic-frame\xff\xd9'
RECORD = video.ImageRecord(100, 0x83, 0x21, 5, 0)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = wire.DueviClient('unused', 'unused', 'unused')
        self.client._connected = True
        self.client._hashed_login = 'synthetic'
        self.client._send_rpc = Mock()
        self.protocol = video.DueviVideoProtocol(self.client)

    def test_empty_capture_success_is_distinct_from_timeout(self):
        self.client._send_rpc.return_value = b''
        self.protocol.request_capture(123)
        request = self.client._send_rpc.call_args.args[0]
        self.assertEqual(request, struct.pack('>IH', 93, 9) + b'synthetic' + struct.pack('>I', 123))
        for response in (None, b'error'):
            self.client._send_rpc.return_value = response
            with self.assertRaises(video.VideoError):
                self.protocol.request_capture(123)

    def test_disconnected_does_not_send(self):
        self.client._connected = False
        with self.assertRaises(video.VideoError):
            self.protocol.request_capture(123)
        self.client._send_rpc.assert_not_called()

    def test_system_status_variable_name_length(self):
        for size in (0, 3, 40):
            for busy in (0, 1):
                response = struct.pack('>HH', 1, size) + b'x' * size + bytes(9) + bytes([busy])
                self.client._send_rpc.return_value = response
                self.assertEqual(self.protocol.is_capturing(), bool(busy))
                self.client._send_rpc.return_value = response[:-1]
                with self.assertRaises(video.VideoError):
                    self.protocol.is_capturing()

    def test_list_flags_and_strict_lengths(self):
        raw = struct.pack('>HIBBBB', 1, 100, 0x83, 0x21, 5, 0)
        self.client._send_rpc.return_value = raw
        self.assertEqual(self.protocol.read_list(), [RECORD])
        self.assertEqual(RECORD.frame_count, 3)
        self.assertTrue(RECORD.is_video_pir)
        for invalid in (b'', raw[:-1], raw + b'x', b'\x01\x01'):
            self.client._send_rpc.return_value = invalid
            with self.assertRaises(video.VideoError):
                self.protocol.read_list()

    def test_chunk_offsets_and_termination(self):
        self.protocol._check_record = Mock()
        chunks = [JPEG[:7], JPEG[7:], b'']
        self.client._send_rpc.side_effect = [struct.pack('>IH', len(c), len(base64.urlsafe_b64encode(c))) + base64.urlsafe_b64encode(c) for c in chunks]
        self.assertEqual(self.protocol.read_frame(2, 1, RECORD), JPEG)
        self.assertEqual([struct.unpack('>BBI', c.args[0][-6:]) for c in self.client._send_rpc.call_args_list],
                         [(2, 1, 0), (2, 1, 7), (2, 1, len(JPEG))])
        self.assertEqual(self.protocol._check_record.call_count, 2)

    def test_rejects_bad_chunks_and_incomplete_jpeg(self):
        self.protocol._check_record = Mock()
        for response in (b'', struct.pack('>IH', 3, 2) + b'xx',
                         struct.pack('>IH', 2, 2) + b'x', bytes(6)):
            self.client._send_rpc.return_value = response
            with self.assertRaises(video.VideoError):
                self.protocol.read_frame(0, 0, RECORD)

    def test_live_chunk_layout_with_synthetic_image_data(self):
        # Observed wire layout: 960 decoded bytes in 1280 Base64 characters.
        # The payload is synthetic, never a photo from the test installation.
        jpeg = b'\xff\xd8' + b'x' * 956 + b'\xff\xd9'
        encoded = base64.urlsafe_b64encode(jpeg)
        self.assertEqual(len(encoded), 1280)
        self.protocol._check_record = Mock()
        self.client._send_rpc.side_effect = [struct.pack('>IH', 960, 1280) + encoded, bytes(6)]
        self.assertEqual(self.protocol.read_frame(0, 0, RECORD), jpeg)
        self.assertEqual(struct.unpack('>BBI', self.client._send_rpc.call_args.args[0][-6:]),
                         (0, 0, 960))

    def test_rejects_invalid_base64_and_wrong_decoded_length(self):
        self.protocol._check_record = Mock()
        for response in (struct.pack('>IH', 3, 4) + b'!!!!',
                         struct.pack('>IH', 4, 4) + b'eHh4'):
            self.client._send_rpc.return_value = response
            with self.assertRaises(video.VideoError):
                self.protocol.read_frame(0, 0, RECORD)

    def test_transfer_limits(self):
        self.protocol._check_record = Mock()
        encoded = base64.urlsafe_b64encode(JPEG)
        self.client._send_rpc.return_value = struct.pack('>IH', len(JPEG), len(encoded)) + encoded
        with patch.object(video, 'MAX_IMAGE_BYTES', 4), self.assertRaises(video.VideoError):
            self.protocol.read_frame(0, 0, RECORD)
        with patch.object(video, 'TRANSFER_TIMEOUT', 0), self.assertRaises(video.VideoError):
            self.protocol.read_frame(0, 0, RECORD)

    def test_rotating_list_or_busy_panel_rejected(self):
        self.protocol.is_capturing = Mock(return_value=False)
        self.protocol.read_list = Mock(return_value=[])
        with self.assertRaises(video.VideoError):
            self.protocol._check_record(0, RECORD)
        self.protocol.read_list.return_value = [RECORD]
        self.protocol.is_capturing.return_value = True
        with self.assertRaises(video.VideoError):
            self.protocol._check_record(0, RECORD)

    def test_rpc_accepts_empty_payload_and_wraps_sequence(self):
        self.client._send_rpc = wire.DueviClient._send_rpc.__get__(self.client)
        self.client._seq = 65535
        hdr = struct.pack('>IIBBBBHH', wire.CP_NSI, 0, 0x16, 0, 0, 0, 65535, 26)
        pkt = hdr + struct.pack('>BBHH', 0x36, 0, 10, 10) + b'\x02\x02\x00\x00'
        sock = Mock()
        sock.recvfrom.side_effect = [BlockingIOError(), (pkt, ('unused', 5570))]
        self.client._sock = sock
        self.assertEqual(self.client._send_rpc(b'test'), b'')
        self.assertEqual(self.client._seq, 0)

    def test_rpc_exception_is_not_empty_capture_success(self):
        self.client._send_rpc = wire.DueviClient._send_rpc.__get__(self.client)
        hdr = struct.pack('>IIBBBBHH', wire.CP_NSI, 0, 0x16, 0, 0, 3, 2, 26)
        pkt = hdr + struct.pack('>BBHH', 0x36, 0, 10, 10) + b'\x02\x02\x00\x00'
        sock = Mock()
        sock.recvfrom.side_effect = [BlockingIOError(), (pkt, ('unused', 5570))]
        self.client._sock = sock
        with self.assertLogs(wire.__name__, level='WARNING'):
            with self.assertRaises(video.VideoError):
                self.protocol.request_capture(123)


class GalleryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hass = Mock()
        async def executor(method, *args):
            return method(*args)
        self.hass.async_add_executor_job = executor
        self.coordinator = coordinator_module.DueviVideoCoordinator(
            self.hass, Mock(), Mock(), {5: {'serial_log': 123}})
        self.protocol = Mock()
        self.coordinator.protocol = self.protocol
        self.protocol.is_capturing.return_value = False
        self.protocol.read_frame.return_value = JPEG

    async def test_latest_three_frames_and_cache_reuse(self):
        old = video.ImageRecord(90, 3, 1, 5, 0)
        unrelated = video.ImageRecord(110, 3, 0, 5, 0)
        records = [old, RECORD, unrelated]
        self.protocol.read_list.return_value = records
        result = await self.coordinator._async_update_data()
        self.assertEqual([s.frame for s in result[5]], [2, 1, 0])
        self.assertEqual(self.protocol.read_frame.call_count, 3)
        self.coordinator.data = result
        self.assertEqual(await self.coordinator._async_update_data(), result)
        self.assertEqual(self.protocol.read_frame.call_count, 3)
        self.protocol.request_capture.assert_not_called()

    async def test_failed_download_preserves_gallery(self):
        previous = self.coordinator.data
        self.protocol.read_list.return_value = [RECORD]
        self.protocol.read_frame.side_effect = video.VideoError('Incomplete JPEG')
        with self.assertRaises(HAError):
            await self.coordinator._async_update_data()
        self.assertIs(self.coordinator.data, previous)

    async def test_gallery_rotates_and_keeps_devices_separate(self):
        other = video.ImageRecord(120, 1, 1, 7, 0)
        newest = video.ImageRecord(110, 1, 1, 5, 0)
        self.coordinator.devices[7] = {'serial_log': 456}
        result = await self.coordinator._gallery([RECORD, newest, other])
        self.assertEqual([(s.record, s.frame) for s in result[5]],
                         [(newest, 0), (RECORD, 2), (RECORD, 1)])
        self.assertEqual([(s.record, s.frame) for s in result[7]], [(other, 0)])
        self.assertEqual(await self.coordinator._gallery([]), {5: (), 7: ()})

    async def test_image_entities_use_cache_without_capturing(self):
        entity = image_module.DueviImage(self.hass, Mock(entry_id='test'), self.coordinator, 5, 0)
        self.assertFalse(entity.available)
        self.assertIsNone(await entity.async_image())
        self.coordinator.data = await self.coordinator._gallery([RECORD])
        self.coordinator.last_update_success = False
        self.assertTrue(entity.available)
        self.assertEqual(await entity.async_image(), JPEG)
        self.assertIsNotNone(entity.image_last_updated.tzinfo)
        self.protocol.request_capture.assert_not_called()
        button = button_module.DueviCaptureButton(Mock(entry_id='test'), self.coordinator, 5)
        self.coordinator.async_capture = AsyncMock()
        await button.async_press()
        self.coordinator.async_capture.assert_awaited_once_with(5)

    async def test_capture_waits_for_new_record(self):
        self.protocol.read_list.side_effect = [[], [], [RECORD]]
        with patch.object(coordinator_module.asyncio, 'sleep', new=AsyncMock()):
            await self.coordinator._capture_once(5)
        self.protocol.request_capture.assert_called_once_with(123)
        self.assertEqual(len(self.coordinator.data[5]), 3)

    async def test_capture_tolerates_a_lost_status_reply_without_retriggering(self):
        self.protocol.is_capturing.side_effect = [False, video.VideoError('No response'), False]
        self.protocol.read_list.side_effect = [[], [RECORD]]
        with patch.object(coordinator_module.asyncio, 'sleep', new=AsyncMock()):
            await self.coordinator._capture_once(5)
        self.protocol.request_capture.assert_called_once_with(123)
        self.assertEqual(len(self.coordinator.data[5]), 3)

    async def test_button_takes_two_photos_and_retains_three(self):
        old = video.ImageRecord(90, 1, 1, 5, 0)
        first = video.ImageRecord(100, 1, 1, 5, 0)
        second = video.ImageRecord(110, 1, 1, 5, 0)
        self.protocol.read_list.side_effect = [
            [old], [old, first], [old, first], [old, first, second]]
        def capture(serial):
            if self.protocol.request_capture.call_count == 2:
                self.assertEqual(self.coordinator.data[5][0].record, first)
        self.protocol.request_capture.side_effect = capture
        with patch.object(coordinator_module.asyncio, 'sleep', new=AsyncMock()):
            await self.coordinator.async_capture(5)
        self.assertEqual(self.protocol.request_capture.call_count, 2)
        self.assertEqual([s.record for s in self.coordinator.data[5]], [second, first, old])

    async def test_second_take_failure_keeps_first_and_does_not_retry(self):
        self.protocol.read_list.side_effect = [[], [RECORD], [RECORD]]
        self.protocol.request_capture.side_effect = [None, video.VideoError('No response')]
        with patch.object(coordinator_module.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaisesRegex(HAError, 'Capture 2 of 2 failed'):
                await self.coordinator.async_capture(5)
        self.assertEqual(self.protocol.request_capture.call_count, 2)
        self.assertEqual(len(self.coordinator.data[5]), 3)
        self.assertFalse(self.coordinator._operation_lock.locked())

    async def test_busy_capture_and_duplicate_press_do_not_send(self):
        self.protocol.is_capturing.return_value = True
        with self.assertRaises(HAError):
            await self.coordinator.async_capture(5)
        self.protocol.is_capturing.return_value = False
        async with self.coordinator._operation_lock:
            with self.assertRaises(HAError):
                await self.coordinator.async_capture(5)
            self.assertIs(await self.coordinator._async_update_data(), self.coordinator.data)
        self.protocol.request_capture.assert_not_called()

    async def test_capture_timeout_does_not_retry_or_discard_images(self):
        self.protocol.read_list.return_value = []
        previous = self.coordinator.data
        with patch.object(coordinator_module, 'CAPTURE_TIMEOUT', 0), self.assertRaises(HAError):
            await self.coordinator.async_capture(5)
        self.protocol.request_capture.assert_called_once()
        self.assertIs(self.coordinator.data, previous)
        self.assertFalse(self.coordinator._operation_lock.locked())


    async def test_unload_cancels_pending_capture(self):
        self.protocol.read_list.return_value = []
        task = asyncio.create_task(self.coordinator.async_capture(5))
        await asyncio.sleep(0)
        await self.coordinator.async_shutdown()
        self.assertTrue(task.cancelled())
        self.assertFalse(self.coordinator._operation_lock.locked())


class AnimationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = Mock()
        self.coordinator.devices = {5: {'name': 'Synthetic sensor'}}
        self.coordinator.data = {5: tuple(types.SimpleNamespace(
            record=RECORD, frame=i, jpeg=bytes([i])) for i in (2, 1, 0))}
        self.entity = camera_module.DueviAnimationCamera(
            Mock(entry_id='test'), self.coordinator, 5)
        self.request = Mock()
        self.request.transport.is_closing.return_value = False

    async def test_stream_replays_cached_frames_chronologically(self):
        async def stream(request, next_frame, content_type, interval):
            self.assertEqual(content_type, 'image/jpeg')
            self.assertEqual(interval, 1)
            return [await next_frame() for _ in range(7)]
        with patch.object(camera_module, 'async_get_still_stream', stream):
            self.assertEqual(await self.entity.handle_async_mjpeg_stream(self.request),
                             [b'\x01', b'\x02', b'\x01', b'\x02', b'\x01', b'\x02', b'\x01'])
        self.coordinator.protocol.assert_not_called()
        self.assertEqual(self.coordinator.method_calls, [])
        self.assertEqual(await self.entity.async_camera_image(), b'\x02')

    async def test_each_viewer_has_an_independent_cursor(self):
        callbacks = []
        async def stream(request, next_frame, content_type, interval):
            callbacks.append(next_frame)
        with patch.object(camera_module, 'async_get_still_stream', stream):
            await self.entity.handle_async_mjpeg_stream(self.request)
            await self.entity.handle_async_mjpeg_stream(self.request)
        self.assertEqual(await callbacks[0](), b'\x01')
        self.assertEqual(await callbacks[0](), b'\x02')
        self.assertEqual(await callbacks[1](), b'\x01')

    async def test_new_gallery_restarts_playback_and_disconnect_stops_it(self):
        async def stream(request, next_frame, content_type, interval):
            self.assertEqual(await next_frame(), b'\x01')
            self.coordinator.data[5] = self.coordinator.data[5][:1]
            self.assertEqual(await next_frame(), b'\x02')
            self.request.transport.is_closing.return_value = True
            self.assertIsNone(await next_frame())
        with patch.object(camera_module, 'async_get_still_stream', stream):
            await self.entity.handle_async_mjpeg_stream(self.request)

    async def test_empty_gallery_and_unload(self):
        self.assertTrue(self.entity.available)
        await self.entity.async_will_remove_from_hass()
        self.assertFalse(self.entity.available)
        self.assertIsNone(await self.entity.async_camera_image())
        self.entity._removed = False
        self.coordinator.data[5] = ()
        self.assertFalse(self.entity.available)
        self.assertIsNone(await self.entity.async_camera_image())

if __name__ == '__main__':
    unittest.main()
