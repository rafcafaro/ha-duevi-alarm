"""Bounded VIDEO-PIR JPEG transfers over the existing panel session.

Protocol: REQUEST_REMOTE_IMAGE (93), READ_IMAGE_LIST (70), READ_IMAGE (71).
The panel returns still frames, not a stream. No images are written to disk.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import struct
import time

from .nabto_udp import DueviClient

VIDEO_PIR_FAMILY = 128
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 2 * 1024 * 1024
TRANSFER_TIMEOUT = 60


class VideoError(Exception):
    """A capture or transfer could not be completed safely."""


@dataclass(frozen=True)
class ImageRecord:
    """Panel list entry; its position is only valid while the list is unchanged."""

    timestamp: int
    count_flags: int
    source_flags: int
    device_index: int
    zone_index: int

    @property
    def frame_count(self) -> int:
        return self.count_flags & 15

    @property
    def is_video_pir(self) -> bool:
        return self.source_flags & 3 == 1


class DueviVideoProtocol:
    """Use the client's RPC lock; never open a second panel session."""

    def __init__(self, client: DueviClient) -> None:
        self.client = client

    def _query(self, query: int, payload: bytes) -> bytes:
        # Construct auth under the lock, so reconnect cannot change it mid-query.
        with self.client._lock:
            if not self.client._connected:
                raise VideoError("Panel is disconnected")
            login = self.client._hashed_login.encode()
            response = self.client._send_rpc(
                struct.pack('>IH', query, len(login)) + login + payload
            )
        if response is None:
            raise VideoError("Panel did not respond")
        return response

    def is_capturing(self) -> bool:
        response = self._query(5, struct.pack('>BI', 2, 0))
        if len(response) < 4:
            raise VideoError("Invalid system status")
        name_length = struct.unpack_from('>H', response, 2)[0]
        offset = 4 + name_length + 9
        if len(response) <= offset:
            raise VideoError("Incomplete system status")
        return bool(response[offset])

    def request_capture(self, serial: int) -> None:
        # Query 93 has an EMPTY successful response. Never retry a capture:
        # a lost reply does not mean the panel failed to start acquisition.
        response = self._query(93, struct.pack('>I', serial))
        if response:
            raise VideoError("Unexpected capture response")

    def read_list(self) -> list[ImageRecord]:
        response = self._query(70, b'\x00')
        if len(response) < 2:
            raise VideoError("Invalid image list")
        count = struct.unpack_from('>H', response)[0]
        if count > 256 or len(response) != 2 + count * 8:
            raise VideoError("Incomplete image list")
        return [ImageRecord(*struct.unpack_from('>IBBBB', response, 2 + i * 8))
                for i in range(count)]

    def read_frame(self, index: int, frame: int, expected: ImageRecord) -> bytes:
        """Fetch one JPEG, rejecting truncation and list rotation during transfer."""
        if not 0 <= index <= 255 or not 0 <= frame < expected.frame_count:
            raise VideoError("Invalid image index")
        self._check_record(index, expected)
        image = bytearray()
        deadline = time.monotonic() + TRANSFER_TIMEOUT
        while time.monotonic() < deadline:
            response = self._query(71, struct.pack('>BBI', index, frame, len(image)))
            if len(response) < 6:
                raise VideoError("Incomplete image chunk")
            size, raw_length = struct.unpack_from('>IH', response)
            if len(response) != 6 + raw_length:
                raise VideoError("Invalid image chunk length")
            # The Nabto raw field contains URL-safe Base64 text. image_size
            # and image_offset count decoded JPEG bytes, not encoded characters.
            try:
                chunk = base64.b64decode(response[6:], altchars=b'-_', validate=True)
            except binascii.Error as error:
                raise VideoError("Invalid image encoding") from error
            if len(chunk) != size:
                raise VideoError("Invalid decoded image chunk length")
            if size == 0:
                if not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
                    raise VideoError("Invalid or incomplete JPEG")
                self._check_record(index, expected)
                return bytes(image)
            if len(image) + size > MAX_IMAGE_BYTES:
                raise VideoError("Image exceeds transfer limit")
            image.extend(chunk)
        raise VideoError("Image transfer timed out")

    def _check_record(self, index: int, expected: ImageRecord) -> None:
        if self.is_capturing():
            raise VideoError("Panel is capturing images")
        records = self.read_list()
        if index >= len(records) or records[index] != expected:
            raise VideoError("Image list changed during transfer")
