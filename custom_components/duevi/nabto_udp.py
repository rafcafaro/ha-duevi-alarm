"""Duevi CE-LAN direct UDP client — pure Python, no dependencies.

Communicates with the CE-LAN alarm panel over local UDP (Nabto Micro protocol).
Implements U_CONNECT, AUTH_SESSION, SETTING_UPDATE (Query 5), and
READ_AREAS_STAT (Query 60) — everything needed for Home Assistant integration.

Protocol derived from network traffic analysis of the Duevi Connect app.
"""
from __future__ import annotations

import hashlib
import logging
import socket
import struct
import time
from typing import Any

from .const import (
    DEFAULT_PORT,
    KEY_LINE_STATE,
    KEY_TX_FLAGS,
    KEY_TX_STATE,
    SM_ALARM,
    SM_ARMED,
    SM_ARMING,
    SM_DISARMED,
    SM_PANIC,
    SM_PENDING,
    SM_TAMPER,
)

_LOGGER = logging.getLogger(__name__)

# Protocol-level constants (Nabto Micro)
CP_NSI = 0x22515913
FP_GUEST = bytes.fromhex("66a51975513e4c39bacb00e37a3af952")



def _sm_to_ha(sm: int, ins_state: int = 15) -> str:
    """Map the panel's sm value to a Home Assistant alarm state string."""
    if sm in SM_DISARMED:
        return "disarmed"
    if sm in SM_ARMING:
        return "arming"
    if sm in SM_ARMED:
        return "armed_away" if (ins_state & 0x0F) == 15 else "armed_home"
    if sm in SM_ALARM:
        return "triggered"
    if sm in SM_PENDING:
        return "pending"
    if sm in SM_TAMPER or sm in SM_PANIC:
        return "triggered"
    return "disarmed"


def _pl(type_id: int, data: bytes) -> bytes:
    """Build a Nabto payload TLV: type(1) + flags(1) + len(2) + data."""
    return struct.pack(">BBH", type_id, 0, 4 + len(data)) + data


class DueviClient:
    """Pure-Python direct UDP client for the Duevi CE-LAN alarm panel."""

    def __init__(
        self,
        host: str,
        email: str,
        pin: str,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._email = email
        self._pin = pin

        self._sock: socket.socket | None = None
        self._nsi_sp: int = 0
        self._seq: int = 2
        self._hashed_login: str = ""
        self._connected: bool = False
        self._last_status: dict[str, Any] | None = None
        self._last_status_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Establish UDP session: U_CONNECT + AUTH + setup commands."""
        self.disconnect()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(("0.0.0.0", 0))

            if not self._u_connect():
                return False
            if not self._auth_session():
                return False
            # Mandatory post-login setup commands
            self._send_query5(0x01)  # read config
            self._send_query5(0x22)  # switch to operative mode
            self._connected = True
            self._last_status_time = 0.0
            _LOGGER.info("Connected to Duevi alarm at %s:%d", self._host, self._port)
            return True
        except Exception:
            _LOGGER.exception("Failed to connect to Duevi alarm")
            self.disconnect()
            return False

    def disconnect(self) -> None:
        """Close the UDP socket."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._nsi_sp = 0
        self._seq = 2
        self._hashed_login = ""
        self._connected = False
        self._last_status_time = 0.0

    def get_status(self) -> dict[str, Any] | None:
        """Poll alarm status via READ_AREAS_STAT (query 60).

        Returns dict with keys: state, sm, ins_state, sect_alarm, open_door, etc.
        Returns None on communication failure.
        """
        if not self._connected:
            return None

        try:
            # Query 60: hashed_login(raw) + index(uint8)
            body = (
                struct.pack(">I", 60)
                + struct.pack(">H", len(self._hashed_login))
                + self._hashed_login.encode()
                + struct.pack(">B", 0)  # area index 0
            )
            resp = self._send_rpc(body)
            if not resp or len(resp) < 12:
                _LOGGER.warning("No/short status response from alarm")
                self._connected = False
                return None

            # Response: list_count(uint16) + fields...
            pos = 2  # skip list count
            no_sound = resp[pos]; pos += 1
            no_alert = resp[pos]; pos += 1
            auto_ins_off = resp[pos]; pos += 1
            sm = resp[pos]; pos += 1
            ins_state = resp[pos]; pos += 1
            reinsert = resp[pos]; pos += 1
            open_door = resp[pos]; pos += 1
            sect_alarm = resp[pos]; pos += 1
            mem_sect_alarm = resp[pos]; pos += 1
            in_out_secs = resp[pos]; pos += 1

            zones = {}
            if pos + 2 <= len(resp):
                pz_count = struct.unpack(">H", resp[pos:pos+2])[0]; pos += 2
                global_idx = 0
                for _ in range(pz_count):
                    if pos + 2 > len(resp): break
                    in_count = struct.unpack(">H", resp[pos:pos+2])[0]; pos += 2
                    for _ in range(in_count):
                        if pos >= len(resp): break
                        flags = resp[pos]; pos += 1
                        
                        # Disabled=2, Excluded=1 -> IF neither is set, zone is "monitored"
                        is_excluded = bool(flags & 1)
                        is_disabled = bool(flags & 2)
                        
                        # State is bits 2,3,4
                        z_state = (flags & 0x1C) >> 2
                        
                        # 0=Normal, 2=Alarm/Open, 3=Tamper, etc.
                        is_open = (z_state == 2) or (z_state == 3) or (z_state == 4)

                        # Only expose zone if it's active & configured (we'll assume all are configured but 
                        # we can just emit everything we see or filter later. We'll emit all 128 here, 
                        # or filter out ones that are permanently zero/unconfigured.)
                        # Actually, if flags != 0, we can definitely assume it's configured. Some might be 0 but valid.
                        # Wait, we'll return all zones and let HA filter them.
                        zones[global_idx] = {
                            "flags": flags,
                            "open": is_open,
                            "disabled": is_disabled,
                            "excluded": is_excluded,
                            "state_id": z_state
                        }
                        global_idx += 1

            self._last_status = {
                "state": _sm_to_ha(sm, ins_state),
                "sm": sm,
                "ins_state": ins_state,
                "sect_alarm": sect_alarm,
                "mem_sect_alarm": mem_sect_alarm,
                "open_door": open_door,
                "in_out_secs": in_out_secs,
                "no_sound": no_sound,
                "no_alert": no_alert,
                "zones": zones,
            }
            self._last_status_time = time.time()
            return self._last_status
        except Exception:
            _LOGGER.exception("Error polling alarm status")
            self._connected = False
            return None

    def arm(self, sectors: int = 15, area: int = 0) -> bool:
        """Arm the alarm. sectors=15 arms all 4 sectors."""
        data = sectors | (area << 4)
        return self._set_area_state(data)

    def arm_partial(self, sectors: int = 1, area: int = 0) -> bool:
        """Arm partial sectors (e.g. perimeter only)."""
        data = sectors | (area << 4)
        return self._set_area_state(data)

    def disarm(self, area: int = 0) -> bool:
        """Disarm the alarm."""
        data = 0 | (area << 4)
        return self._set_area_state(data)

    # ------------------------------------------------------------------
    # Private: high-level operations
    # ------------------------------------------------------------------

    def _set_area_state(self, data: int) -> bool:
        """Send area state change command (cmd=0x26) and check answer."""
        resp = self._send_query5(0x26, data)
        if resp and len(resp) >= 1:
            answer = resp[0] >> 4
            if answer == 1:  # command accepted
                return True
            _LOGGER.error("Set area state failed: answer=%d", answer)
        return False

    def _send_query5(self, cmd: int, data: int = 0) -> bytes:
        """Build and send a SETTING_UPDATE (query 5) command."""
        body = (
            struct.pack(">I", 5)
            + struct.pack(">H", len(self._hashed_login))
            + self._hashed_login.encode()
            + struct.pack(">B", cmd)
            + struct.pack(">I", data)
        )
        return self._send_rpc(body)

    # ------------------------------------------------------------------
    # Private: connection & auth
    # ------------------------------------------------------------------

    def _u_connect(self) -> bool:
        """Send U_CONNECT and parse NSI_SP from response."""
        # IPX: IP(4)+Port(2)+IP(4)+Port(2)+NatType(1) = 13 bytes, all zeros + 0xa0
        ipx = struct.pack(">IHIHB", 0, 0, 0, 0, 0xA0)
        cp_id = bytes([0x01]) + b"guest"
        fp = bytes([0x01]) + FP_GUEST

        body = _pl(0x35, ipx) + _pl(0x3F, cp_id) + _pl(0x4B, fp)
        hdr = struct.pack(">IIBBBBHH", CP_NSI, 0, 0x83, 2, 0, 0, 0, 16 + len(body))

        self._sock.sendto(hdr + body, (self._host, self._port))
        self._sock.settimeout(3.0)

        try:
            data, _ = self._sock.recvfrom(2048)
        except socket.timeout:
            _LOGGER.error("U_CONNECT timeout — alarm not reachable at %s:%d", self._host, self._port)
            return False

        if len(data) < 16 or data[8] != 0x83:
            _LOGGER.error("U_CONNECT: unexpected response type 0x%02x", data[8] if len(data) > 8 else 0)
            return False

        # Parse NOTIFY payload (0x34) to extract NSI_SP
        pos = 16
        while pos + 4 <= len(data):
            pt = data[pos]
            pl = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
            if pt == 0x34 and pl >= 12:
                self._nsi_sp = struct.unpack(">I", data[pos + 8 : pos + 12])[0]
                break
            pos += pl

        if not self._nsi_sp:
            _LOGGER.error("U_CONNECT: failed to extract NSI_SP")
            return False

        # Drain any extra packets
        self._sock.setblocking(False)
        while True:
            try:
                self._sock.recv(2048)
            except BlockingIOError:
                break

        _LOGGER.debug("U_CONNECT OK: NSI_SP=%d", self._nsi_sp)
        return True

    def _auth_session(self) -> bool:
        """Two-step AUTH_SESSION: get hash, then login."""
        # Step 1: logcmd=0 — get hashed_session
        qry = (
            struct.pack(">I", 4)
            + struct.pack(">H", len(self._email))
            + self._email.encode()
            + bytes([0])
            + struct.pack(">H", 1)
            + b"0"
        )
        resp = self._send_rpc(qry)
        if not resp or len(resp) < 8:
            _LOGGER.error("AUTH_SESSION logcmd=0 failed")
            return False

        # hashed_session is the LAST 4 bytes of the response
        hashed_session = struct.unpack(">I", resp[-4:])[0]
        _LOGGER.debug("hashed_session: %08X", hashed_session)

        # Compute hashed_login = MD5(email + "+" + hex(session) + "+" + pin)
        plain = f"{self._email}+{hashed_session:08X}+{self._pin}"
        self._hashed_login = hashlib.md5(plain.encode()).hexdigest().upper()

        # Step 2: logcmd=1 — authenticate
        qry = (
            struct.pack(">I", 4)
            + struct.pack(">H", len(self._email))
            + self._email.encode()
            + bytes([1])
            + struct.pack(">H", len(self._hashed_login))
            + self._hashed_login.encode()
        )
        resp = self._send_rpc(qry)
        if not resp or resp[0] != 2:
            _LOGGER.error("AUTH_SESSION logcmd=1 failed: stato=%d", resp[0] if resp else -1)
            return False

        _LOGGER.info("Authenticated with Duevi alarm (stato=2)")
        return True

    # ------------------------------------------------------------------
    # Private: packet I/O
    # ------------------------------------------------------------------

    def _send_rpc(self, rpc_body: bytes) -> bytes:
        """Encode, send NP_DATA, and wait for the crypto response."""
        if not self._sock:
            return b""

        # Drain any stale packets from the socket before sending
        self._sock.setblocking(False)
        while True:
            try:
                stale, _ = self._sock.recvfrom(4096)
                _LOGGER.debug("drained stale packet: %d bytes", len(stale))
            except BlockingIOError:
                break
            except Exception:
                break

        # Pad to even + always add 2 pad bytes
        pad = 2 - (len(rpc_body) % 2)
        if pad == 0:
            pad = 2
        padded = rpc_body + bytes([pad] * pad)

        algo = struct.pack(">H", 0x000A)  # CRYPT_W_NULL_DATA
        pl_len = 4 + len(algo) + len(padded) + 2  # crypto hdr + algo + body + checksum
        pkt_len = 16 + pl_len

        my_seq = self._seq
        hdr = struct.pack(
            ">IIBBBBHH", CP_NSI, self._nsi_sp, 0x16, 0, 0, 0, my_seq, pkt_len
        )
        c_hdr = struct.pack(">BBH", 0x36, 0, pl_len)

        chk = sum(hdr + c_hdr + algo + padded) & 0xFFFF
        pkt = hdr + c_hdr + algo + padded + struct.pack(">H", chk)

        self._seq += 1
        self._sock.sendto(pkt, (self._host, self._port))

        # Wait for the crypto response (skip ACK packets)
        end = time.time() + 5.0
        self._sock.settimeout(1.0)
        while time.time() < end:
            try:
                data, _ = self._sock.recvfrom(4096)
                if len(data) < 16:
                    continue

                pkt_type = data[8]
                resp_seq = struct.unpack(">H", data[12:14])[0]
                _LOGGER.debug("recv %d bytes type=0x%02x seq=%d (expect=%d)",
                              len(data), pkt_type, resp_seq, my_seq)

                if pkt_type != 0x16:
                    continue  # skip ACKs and other non-data packets

                # Parse NP_DATA payload
                pos = 16
                while pos + 4 <= len(data):
                    pt = data[pos]
                    pl = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
                    if pt == 0x36 and pl > 4:
                        enc = data[pos + 4 : pos + pl]
                        if len(enc) >= 4:
                            # strip algo(2) and checksum(2), then remove padding
                            raw = enc[2:-2]
                            if len(raw) > 0:
                                pc = raw[-1]
                                if 0 < pc <= 2 and pc <= len(raw):
                                    return raw[:-pc]
                                return raw
                    pos += pl
            except socket.timeout:
                pass
            except Exception:
                _LOGGER.exception("Error receiving RPC response")
                break

        return b""

    # ------------------------------------------------------------------
    # Sensor queries (used by binary_sensor.py)
    # ------------------------------------------------------------------

    def _send_sensor_query(self, query_id: int, index: int = 0) -> bytes:
        """Send a generic sensor query with hashed_login + index byte."""
        body = (
            struct.pack(">I", query_id)
            + struct.pack(">H", len(self._hashed_login))
            + self._hashed_login.encode()
            + struct.pack(">B", index)
        )
        return self._send_rpc(body)

    def read_input_cfg(self, index: int) -> dict[str, Any] | None:
        """Read config for a single input zone (query 53).

        Returns dict with: name, type, technology, mode, hw_dev_index, etc.
        Returns None if slot is empty or on error.
        """
        resp = self._send_sensor_query(53, index)
        if not resp or len(resp) < 4:
            return None

        pos = 0
        if pos + 2 > len(resp):
            return None
        name_len = struct.unpack(">H", resp[pos:pos+2])[0]; pos += 2
        if pos + name_len > len(resp):
            return None
        name = resp[pos:pos+name_len].decode("utf-8", errors="replace").rstrip("\x00")
        pos += name_len

        if pos + 7 > len(resp):
            return None
        inp_type = resp[pos]; pos += 1
        technology = resp[pos]; pos += 1
        level = resp[pos]; pos += 1
        mode = resp[pos]; pos += 1
        hw_dev_index = resp[pos]; pos += 1
        hw_dev_subidx = resp[pos]; pos += 1
        exclusion = resp[pos]; pos += 1

        # Skip disabled zones (type=0 means DISABLED)
        if inp_type == 0:
            return None

        return {
            "name": name,
            "type": inp_type,
            "technology": technology,
            "level": level,
            "mode": mode,
            "hw_dev_index": hw_dev_index,
            "hw_dev_subidx": hw_dev_subidx,
            "exclusion": exclusion,
        }

    def read_inputs_stat(self) -> list[dict[str, int]] | None:
        """Read live status for ALL inputs at once (query 54).

        Returns list of dicts with: line_state, tx_state, tx_flags
        Returns None on communication failure.
        """
        resp = self._send_sensor_query(54, 0)
        if not resp or len(resp) < 2:
            return None

        pos = 0
        count = struct.unpack(">H", resp[pos:pos+2])[0]; pos += 2
        if count == 0:
            return None  # panel returned empty (session exhausted)

        stats = []
        for _ in range(count):
            if pos + 3 > len(resp):
                break
            line_state = resp[pos]; pos += 1
            tx_state = resp[pos]; pos += 1
            tx_flags = resp[pos]; pos += 1
            stats.append({
                KEY_LINE_STATE: line_state,
                KEY_TX_STATE: tx_state,
                KEY_TX_FLAGS: tx_flags,
            })
        return stats

    def read_device_cfg(self, index: int) -> dict[str, Any] | None:
        """Read config for a single physical device (query 56).

        Returns dict with: name, family, nbr_inputs, nbr_outputs, fw_version
        Returns None if slot is empty (transport=7) or on error.
        """
        resp = self._send_sensor_query(56, index)
        if not resp or len(resp) < 4:
            return None

        pos = 0
        name_len = struct.unpack(">H", resp[pos:pos+2])[0]; pos += 2
        if pos + name_len > len(resp):
            return None
        name = resp[pos:pos+name_len].decode("utf-8", errors="replace").rstrip("\x00")
        pos += name_len

        if pos + 12 > len(resp):
            return None
        serial_log = struct.unpack(">I", resp[pos:pos+4])[0]; pos += 4
        transport = resp[pos]; pos += 1
        no_superv = resp[pos]; pos += 1
        dev_type = resp[pos]; pos += 1
        family = resp[pos]; pos += 1
        nbr_inputs = resp[pos]; pos += 1
        nbr_outputs = resp[pos]; pos += 1
        fw_version = struct.unpack(">H", resp[pos:pos+2])[0]; pos += 2

        # transport=7 means empty/unconfigured slot
        if transport == 7:
            return None

        return {
            "name": name,
            "family": family,
            "nbr_inputs": nbr_inputs,
            "nbr_outputs": nbr_outputs,
            "fw_version": fw_version,
        }
