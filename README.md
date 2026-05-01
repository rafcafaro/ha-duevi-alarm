# Duevi CE-LAN Alarm — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration for **Duevi CE-LAN** alarm panels. Communicates entirely over local UDP — no cloud, no relay, no external dependencies.

## Features

- **Alarm Control Panel** — Arm (Away / Home) and Disarm
- **Binary Sensors** — Real-time status for PIR motion sensors, magnetic door/window contacts, and vibration sensors (1-second polling)
- **100% Local** — Direct UDP communication with the panel via the Nabto Micro protocol
- **Zero Dependencies** — Pure Python, no external libraries required

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
