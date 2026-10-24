# SPDX-License-Identifier: MIT
"""Receive-only Linux IP_PKTINFO admission. Exact raw custody precedes normalization."""

import ipaddress
import socket
import struct
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from .parse import MAX_DATAGRAM_BYTES, Packet, decode_envelope
from .spool import Receipt, Spool, SpoolError


@dataclass(frozen=True)
class AdmissionConfig:
    interface_index: int
    hub_source_ip: str = field(repr=False)
    hub_identity: str = field(repr=False)
    device_identities: frozenset[str] = field(repr=False)
    hub_source_port: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.interface_index <= 0 or ipaddress.ip_address(self.hub_source_ip).version != 4:
            raise ValueError("Invalid private receiver network configuration")
        if not self.hub_identity or not self.device_identities:
            raise ValueError("Missing private receiver identity configuration")


@dataclass(frozen=True)
class Datagram:
    raw: bytes = field(repr=False)
    interface_index: int
    source_ip: str = field(repr=False)
    source_port: int = field(repr=False)
    received_ns: int
    monotonic_ns: int
    truncated: bool = False


def identity_matches(packet: Packet, config: AdmissionConfig) -> bool:
    if packet.envelope is None:
        # Identity cannot be claimed from malformed bytes; these remain raw-only quarantine.
        return packet.state == "quarantined"
    envelope = packet.envelope
    serial = envelope.get("serial_number")
    hub = envelope.get("hub_sn")
    if hub is not None and hub != config.hub_identity:
        return False
    if packet.family == "hub_status":
        return serial == config.hub_identity
    # v171's obs_st envelope has no hub_sn. Source + configured device still must match.
    return isinstance(serial, str) and serial in config.device_identities


class Receiver:
    def __init__(self, config: AdmissionConfig, spool: Spool) -> None:
        self.config, self.spool = config, spool
        self.counts: Counter[str] = Counter()
        self.last_received_ns: dict[str, int] = {}
        self._kernel_overflow = 0

    def accept(self, datagram: Datagram) -> Receipt | None:
        self.counts["received"] += 1
        reason = None
        if datagram.truncated or len(datagram.raw) > MAX_DATAGRAM_BYTES:
            reason = "truncated"
        elif datagram.interface_index != self.config.interface_index:
            reason = "wrong_interface"
        elif datagram.source_ip != self.config.hub_source_ip or (
            self.config.hub_source_port is not None
            and datagram.source_port != self.config.hub_source_port
        ):
            reason = "wrong_source"
        if reason:
            self.counts["rejected_" + reason] += 1
            return None
        packet = decode_envelope(datagram.raw)
        if not identity_matches(packet, self.config):
            self.counts["rejected_wrong_identity"] += 1
            return None
        receipt = Receipt.create(
            datagram.raw,
            self.spool.incarnation,
            datagram.received_ns,
            datagram.monotonic_ns,
            datagram.interface_index,
            datagram.source_ip,
            datagram.source_port,
            quarantine_reason=packet.reason if packet.state == "quarantined" else None,
        )
        try:
            if not self.spool.append(receipt):
                self.counts["capacity_loss"] += 1
                return None
        except SpoolError:
            self.counts["spool_write_uncertain"] += 1
            return None
        self.counts["spooled"] += 1
        self.last_received_ns[packet.family] = datagram.received_ns
        if packet.state == "quarantined":
            self.counts["quarantined"] += 1
        return receipt

    def run(self, stop: threading.Event) -> None:
        if sys.platform != "linux":
            raise RuntimeError("Live UDP reception requires the commissioned Linux pod")
        ip_pktinfo = getattr(socket, "IP_PKTINFO", 8)
        rxq_overflow = getattr(socket, "SO_RXQ_OVFL", 40)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setsockopt(socket.IPPROTO_IP, ip_pktinfo, 1)
            udp.setsockopt(socket.SOL_SOCKET, rxq_overflow, 1)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            udp.settimeout(1)
            udp.bind((str(ipaddress.IPv4Address(0)), 50222))
            while not stop.is_set():
                try:
                    raw, ancillary, flags, source = udp.recvmsg(
                        MAX_DATAGRAM_BYTES, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(4)
                    )
                except TimeoutError:
                    continue
                received_ns, mono_ns = time.time_ns(), time.monotonic_ns()
                interface = 0
                for level, kind, value in ancillary:
                    if level == socket.IPPROTO_IP and kind == ip_pktinfo and len(value) >= 12:
                        interface = struct.unpack("=I4s4s", value[:12])[0]
                    elif level == socket.SOL_SOCKET and kind == rxq_overflow and len(value) >= 4:
                        current = struct.unpack("=I", value[:4])[0]
                        self.counts["kernel_overflow"] += (current - self._kernel_overflow) % (
                            1 << 32
                        )
                        self._kernel_overflow = current
                self.accept(
                    Datagram(
                        raw,
                        interface,
                        source[0],
                        source[1],
                        received_ns,
                        mono_ns,
                        bool(flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)),
                    )
                )
                # There is deliberately no send/sendto: DB draining runs in another thread/task.

    def packet_age(self, family: str, now_ns: int) -> Decimal | None:
        received = self.last_received_ns.get(family)
        return Decimal(now_ns - received) / Decimal(1_000_000_000) if received is not None else None
