# Duevi CE-LAN Alarm — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration for **Duevi CE-LAN** alarm panels. Communicates entirely over local UDP — no cloud, no relay, no external dependencies.

## Features

- **Alarm Control Panel** — Arm (Away / Home) and Disarm
- **Binary Sensors** — Real-time status for PIR motion sensors, magnetic door/window contacts, and vibration sensors (1-second polling)
- **100% Local** — Direct UDP communication with the panel via the Nabto Micro protocol
- **Zero Dependencies** — Pure Python, no external libraries required
- **Device diagnostics** — Battery percentage, tamper, missing device, temperature and radio quality. Device status is fetched once every 30 seconds, shared across both entity platforms.
- **VIDEO-PIR images** — A capture button and the latest three JPEG frames for each discovered VIDEO-PIR, served through Home Assistant's image entities.

Battery values are the last reports stored by the panel, not direct measurements of sleeping radio devices. Batteries are created only for peripherals advertising battery capability. A zero radio battery with no packet reception recorded is treated conservatively as unknown; a received zero remains 0%. Battery level is exposed only as a percentage; use a numeric-state automation for your preferred low-battery threshold. Failed diagnostic polls make diagnostic entities unavailable until communication recovers. The existing fast zone polling is preserved.

Updating from the trial version 1.0.2 removes its redundant Low Battery entities from the entity registry when the integration loads. Percentage sensors keep their existing IDs.

Offline regression tests: `python -m unittest discover -s tests -v`.

## Installation

### HACS (Recommended)

1. Open **HACS** in Home Assistant.
2. Go to **Integrations** → ⋮ (top right) → **Custom repositories**.
3. Add this repository URL and select **Integration** as the category.
4. Click **Install**.
5. Restart Home Assistant.

### Manual

Copy the `custom_components/duevi` directory into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. In Home Assistant, go to **Settings** → **Devices & Services**.
2. Click **+ Add Integration**.
3. Search for **Duevi CE-LAN Alarm**.
4. Enter your panel details:
   - **Host** — IP address of your CE-LAN panel on the local network
   - **Email** — Your Duevi Connect app login email
   - **PIN** — Your Duevi Connect app password/PIN
   - **Port** — `5570` (default, rarely needs changing)
5. Click **Submit**.

The integration will verify your credentials over the local network. If successful, the alarm panel and all connected sensors will appear immediately.

## Supported Hardware

- **Duevi CE-LAN** alarm panels with Nabto Micro (uNabto) firmware
- All sensor types connected to the panel: PIR, reed switches, vibration sensors, contacts

> **Note:** This integration was developed and tested with CE-LAN firmware v2.12. Other firmware versions may work but are untested.

## Entities Created

| Entity Type | Description |
|-------------|-------------|
| `alarm_control_panel` | Main alarm panel — arm/disarm with state tracking |
| `binary_sensor` (motion) | PIR sensors — 1s polling catches short-lived triggers |
| `binary_sensor` (door) | Reed switch contacts |
| `binary_sensor` (window) | Window contacts and vibration sensors |
| `button` (VIDEO-PIR) | Request two consecutive image captures |
| `image` (VIDEO-PIR) | Latest image and up to two previous frames |
| `camera` (VIDEO-PIR) | Looping animation of the latest two cached images |

## VIDEO-PIR captures

For each configured VIDEO-PIR (device family 128), the integration creates a
**Capture 2 images** button, **Latest image**, **Previous image 1**, and
**Previous image 2**. Press the button from the device page or call `button.press`
in an automation. The configured Duevi account must have video access to that
device. Each press requests two consecutive captures, waiting for the first to
complete and download before starting the second. Each capture may take up to
two minutes. If either fails, the sequence stops and already downloaded images
remain available. Two takes use two camera activations; the interval depends on
the sensor and transfer time, rather than a fixed video frame rate.

VIDEO-PIR returns a sequence of JPEG still frames. The Duevi app animates these
frames; this integration exposes the newest three individual frames, not a live
video stream. A single capture can replace all three gallery slots. Newest frames
are shown first. IP cameras are not included.

The **Recent images animation** camera replays only the latest two cached photos in
chronological order: **oldest → newest**, then repeats, with one second per
frame. After two single-frame takes, the gallery also retains one older photo,
which is excluded from the animation.
It does not reproduce the time gaps between separate captures. Viewing
this replay never wakes the sensor or requests another capture. With only one
stored image, the preview remains static.

To show the animation in a dashboard, use a **Picture entity** card with the
camera's view set to **Live** (this selects animated playback in HA; the content
is still stored photos):

```yaml
type: picture-entity
entity: camera.video_pir_recent_images_animation
camera_view: live
name: Recent photos — oldest to newest
show_state: false
```

Existing images are read when the integration loads and checked every 60 seconds,
including captures initiated by the panel or the Duevi app. Viewing an image does
not initiate a capture. Until images are available, empty slots are unavailable.
The image entity timestamp indicates when Home Assistant downloaded that frame.

Add each image entity to a **Picture entity** dashboard card, and add the capture
button to an **Entities** card. Replace these example entity IDs with the IDs
assigned by your installation:

```yaml
type: vertical-stack
cards:
  - type: entities
    entities:
      - button.video_pir_capture_image
  - type: picture-entity
    entity: image.video_pir_latest_image
  - type: picture-entity
    entity: image.video_pir_previous_image_1
  - type: picture-entity
    entity: image.video_pir_previous_image_2
```

Only three frames per VIDEO-PIR are cached in memory. They are served through
Home Assistant's image/camera proxies, never placed in a public `/local/` directory or
written to disk by this integration. On restart or reload the cache is rebuilt
from the panel. This limit does not delete or change the panel's own image history.
Cached photos remain viewable during connection failures; an unsuccessful capture
does not replace them. Concurrent capture requests are rejected; wait for the
current operation to finish before trying again.

If capture fails, check the sensor's radio reception and the Duevi account's video
permissions. A timeout can also mean that a reply was lost; captures are never
automatically retried, to avoid requesting duplicate images.

For a standalone local protocol check, run `python scripts/check_video.py` with
`DUEVI_HOST` and `DUEVI_EMAIL` in your process environment. The script prompts for
the PIN/password unless `DUEVI_PIN` is set. It only reads existing images by
default; add `--capture` to request one capture. Use `--device-index` when more
than one VIDEO-PIR is configured. Temporarily close other panel clients before
this standalone check to avoid session conflicts. It prints counts and byte
lengths, never credentials, device names or image contents, and saves no images.

See [VIDEO-PIR protocol](docs/video_protocol.md) for the wire format, replay
behavior and testing instructions.

## How It Works

The integration communicates directly with the CE-LAN panel over local UDP port 5570 using the Nabto Micro protocol. The protocol details were derived from network traffic analysis. No cloud relay or Nabto SDK is needed — the entire communication is pure Python.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Integration can't connect | Ensure the panel IP is correct and reachable from HA on UDP port 5570 |
| Sensors show "Unavailable" | The panel may have lost connectivity — check network and panel power |
| State flickering in history | Update to the latest version — anti-flicker logic holds state during transient UDP drops |

## License

[MIT](LICENSE)
