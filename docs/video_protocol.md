# VIDEO-PIR protocol

## Wire format

All operations use the integration's existing authenticated Nabto UDP session.

| Query | Request after the length-prefixed login | Response |
| --- | --- | --- |
| 5, command 2 | command `uint8`, data `uint32` = 0 | System status, including `MakingSNAPSHOT` |
| 93 | Physical device serial `uint32` | Empty successful response |
| 70 | Offset `uint8` = 0 | Count `uint16`, then image records |
| 71 | Record index `uint8`, frame `uint8`, byte offset `uint32` | Decoded size `uint32`, raw-field length `uint16`, Base64 text |

Integers are big-endian. Image records contain a packed timestamp `uint32`,
frame-count flags, source flags, device index and zone index (four `uint8`s).
The frame count is the low nibble; a manual-capture flag occupies bit 7.
VIDEO-PIR has device family 128 and image source type 1 (low two source bits).

The image raw field is URL-safe Base64 text, not binary JPEG data. Both
`image_size` and the next `image_offset` count **decoded bytes**. A size-zero
chunk ends the transfer. For example, 960 decoded bytes occupy 1280 Base64
characters. Tests exercise this layout with synthetic data.

The gallery follows the panel's list order in reverse, with the newest frame
first, and retains at most three frames per VIDEO-PIR. Before and after each
download, the code checks that the record still identifies the same capture.
Requests are serialized with the existing RPC lock; acquisition waits release
the lock so regular alarm polling can continue. A lost status response is
retried within the capture deadline, but a capture command is never resent.

## Manual capture

One button press issues two sequential query 93 requests. Each take must finish
and its images must be downloaded before the next request is sent. There is no
frame-count parameter in query 93; these are separate captures. A failed take
stops the sequence without retrying the command, retaining downloaded images.
Periodic gallery refreshes and camera playback never initiate captures.

## Animation

The camera entity replays only the latest two cached JPEGs as MJPEG, oldest to newest,
at one frame per second. Each viewer has an independent playback cursor. When
the gallery changes, playback restarts from the older of those two frames. The
static camera preview shows the newest frame. Neither endpoint polls the panel
or requests a capture. Closing the viewer or unloading the entity stops playback.

The replay uses fixed timing; gaps between separate captures are not reproduced.
It is a slideshow of up to two stored photos, not a continuous video recording.

## Testing

Run offline checks with `python -m unittest discover -s tests -v`. For an
installation-independent local check, run `python scripts/check_video.py --help`.
