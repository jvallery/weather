# SPDX-License-Identifier: MIT
"""Receive-only Linux admission by interface and well-formed Tempest identity (P4)."""

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

PORT = 50222
IP_PKTINFO = getattr(socket, "IP_PKTINFO", 8)
SO_RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40)
SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)


@dataclass(frozen=True)
class AdmissionConfig:
    interface_index: int
    hub_identity: str = field(repr=False)
    device_identities: frozenset[str] = field(repr=False)
    hub_source_ip: str | None = field(default=None, repr=False)
    hub_source_port: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.interface_index <= 0 or (
            self.hub_source_ip is not None
            and ipaddress.ip_address(self.hub_source_ip).version != 4
        ):
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
    truncated: bool = False


def identity_check(packet: Packet, config: AdmissionConfig) -> tuple[bool, str | None]:
    """Admit only well-formed envelopes naming a configured identity; never infer IDs."""
    if packet.envelope is None:
        return False, None
    envelope = packet.envelope
    serial, hub = envelope.get("serial_number"), envelope.get("hub_sn")
    if "hub_sn" in envelope and hub != config.hub_identity:
        return False, None
    if packet.family == "hub_status":
        return serial == config.hub_identity, None
    if packet.family == "unknown" and serial == config.hub_identity:
        return True, None
    if not isinstance(serial, str) or serial not in config.device_identities:
        return False, None
    if hub is None:
        # v171 examples are not a normative required-field schema. Retain raw evidence,
        # but do not normalize an identity gap absent a commissioned compatibility policy.
        return True, "missing_hub_identity"
    return True, None


def pktinfo_interface(ancillary: list[tuple[int, int, bytes]]) -> int:
    """Arrival interface index from IP_PKTINFO; 0 when absent."""
    for level, kind, value in ancillary:
        if level == socket.IPPROTO_IP and kind == IP_PKTINFO and len(value) >= 12:
            return int(struct.unpack("=I4s4s", value[:12])[0])
    return 0


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
            reason = "oversized"
        elif datagram.interface_index != self.config.interface_index:
            reason = "wrong_interface"
        elif (
            self.config.hub_source_ip is not None
            and datagram.source_ip != self.config.hub_source_ip
        ) or (
            self.config.hub_source_port is not None
            and datagram.source_port != self.config.hub_source_port
        ):
            reason = "wrong_source"
        if reason:
            self.counts["rejected_" + reason] += 1
            return None
        packet = decode_envelope(datagram.raw)
        if packet.envelope is None:
            self.counts["rejected_malformed"] += 1
            return None
        admitted, quarantine_reason = identity_check(packet, self.config)
        if not admitted:
            self.counts["rejected_wrong_identity"] += 1
            return None
        receipt = Receipt.create(
            datagram.raw,
            self.spool.incarnation,
            datagram.received_ns,
            quarantine_reason=quarantine_reason,
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
        if quarantine_reason is not None:
            self.counts["quarantined"] += 1
        return receipt

    def receive(self, udp: socket.socket) -> None:
        """Handle one datagram from a socket with IP_PKTINFO and SO_RXQ_OVFL enabled."""
        raw, ancillary, flags, source = udp.recvmsg(
            MAX_DATAGRAM_BYTES, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(4)
        )
        received_ns = time.time_ns()
        for level, kind, value in ancillary:
            if level == socket.SOL_SOCKET and kind == SO_RXQ_OVFL and len(value) >= 4:
                current = struct.unpack("=I", value[:4])[0]
                self.counts["kernel_overflow"] += (current - self._kernel_overflow) % (1 << 32)
                self._kernel_overflow = current
        self.accept(
            Datagram(
                raw,
                pktinfo_interface(ancillary),
                source[0],
                source[1],
                received_ns,
                bool(flags & socket.MSG_TRUNC),
            )
        )

    def run(self, stop: threading.Event, interface_name: str, port: int = PORT) -> None:
        if sys.platform != "linux":
            raise RuntimeError("Live UDP reception requires the commissioned Linux pod")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            # The kernel enforces the NAD interface; the PKTINFO check stays as a second guard.
            udp.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, interface_name.encode())
            udp.setsockopt(socket.IPPROTO_IP, IP_PKTINFO, 1)
            udp.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            udp.settimeout(1)
            # A wildcard bind is required to receive broadcasts on the bound device.
            udp.bind((str(ipaddress.IPv4Address(0)), port))
            while not stop.is_set():
                try:
                    self.receive(udp)
                except TimeoutError:
                    continue
                # There is deliberately no send/sendto: DB draining runs in another thread/task.

    def packet_age(self, family: str, now_ns: int) -> Decimal | None:
        received = self.last_received_ns.get(family)
        return Decimal(now_ns - received) / Decimal(1_000_000_000) if received is not None else None

