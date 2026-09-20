"""Check VIDEO-PIR locally without HA, saving images or printing device details.

Provide DUEVI_HOST, DUEVI_EMAIL and optionally DUEVI_PIN/DUEVI_PORT in the
environment. Omit --capture to only read existing images. Close other panel
clients first: the panel has limited concurrent session support.
"""
import argparse
import getpass
import importlib
import logging
import os
from pathlib import Path
import sys
import time
import types


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true', help='Request one new capture')
    parser.add_argument('--device-index', type=int, help='Required if multiple VIDEO-PIRs exist')
    args = parser.parse_args()
    host, email = os.environ.get('DUEVI_HOST'), os.environ.get('DUEVI_EMAIL')
    if not host or not email:
        parser.error('Set DUEVI_HOST and DUEVI_EMAIL in the environment')
    pin = os.environ.get('DUEVI_PIN') or getpass.getpass('Duevi PIN/password: ')
    package = types.ModuleType('duevi_check')
    package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/duevi')]
    sys.modules[package.__name__] = package
    wire = importlib.import_module('duevi_check.nabto_udp')
    video = importlib.import_module('duevi_check.video')
    logging.disable(logging.CRITICAL)
    panel = wire.DueviClient(host, email, pin, int(os.environ.get('DUEVI_PORT', '5570')))
    try:
        if not panel.connect():
            raise video.VideoError('Panel connection failed')
        stats = panel.read_devices_stat()
        if stats is None:
            raise video.VideoError('Device discovery failed')
        devices = {}
        for index in range(min(len(stats), 256)):
            cfg = panel.read_device_cfg(index)
            if cfg and cfg['family'] == video.VIDEO_PIR_FAMILY:
                devices[index] = cfg
        print('VIDEO-PIR devices:', len(devices))
        index = args.device_index
        if index is None and len(devices) == 1:
            index = next(iter(devices))
        if index not in devices:
            raise video.VideoError('Select a configured VIDEO-PIR using --device-index')
        protocol = video.DueviVideoProtocol(panel)
        if protocol.is_capturing():
            raise video.VideoError('Panel is already capturing images')
        records = protocol.read_list()
        if args.capture:
            before = records
            protocol.request_capture(devices[index]['serial_log'])
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                time.sleep(2)
                try:
                    if protocol.is_capturing():
                        continue
                    records = protocol.read_list()
                except (video.VideoError, OSError):
                    # Standalone checks have no HA coordinator to reconnect.
                    # Reconnect for reads only; never resend capture requests.
                    panel.connect()
                    continue
                if any(r.is_video_pir and r.device_index == index and r.frame_count
                       and r not in before for r in records):
                    print('New capture completed')
                    break
            else:
                raise video.VideoError('Capture timed out; not retried')
        downloaded = 0
        for position in range(len(records) - 1, -1, -1):
            record = records[position]
            if not record.is_video_pir or record.device_index != index:
                continue
            for frame in range(record.frame_count - 1, -1, -1):
                jpeg = protocol.read_frame(position, frame, record)
                downloaded += 1
                print(f'Frame {downloaded}: {len(jpeg)} bytes, complete JPEG markers')
                if downloaded == video.MAX_IMAGES:
                    return
        if not downloaded:
            print('No stored images; use --capture to request one')
    except (video.VideoError, OSError, ValueError) as error:
        # Avoid printing network addresses or credentials from exception details.
        detail = str(error) if isinstance(error, video.VideoError) else type(error).__name__
        print('VIDEO-PIR check failed:', detail, file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        panel.disconnect()


if __name__ == '__main__':
    main()
