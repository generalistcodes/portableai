"""Advertise PortableAI on the LAN via mDNS / Bonjour.

Uses the pure-Python ``zeroconf`` library so Linux and macOS share one
code path — unlike LAN-IP detection, which has separate OS helpers. A
phone can then list every running server and tell them apart by
hostname in the TXT record.
"""
from __future__ import annotations

import socket
import sys
from typing import Callable

SERVICE_TYPE = "_portableai._tcp.local."

# Virtual / point-to-point names a phone on WiFi cannot reach. Matched
# against ifaddr's adapter name so this stays one code path on every OS.
_SKIP_IFACE_PREFIXES = (
    "lo",
    "docker",
    "veth",
    "br-",
    "virbr",
    "cni",
    "flannel",
    "awdl",
    "llw",
    "utun",
    "vmenet",
    "vmnet",
)


def txt_records(hostname: str, version: str) -> dict[str, str]:
    """TXT keys a phone uses to label a discovered server."""
    return {"name": hostname, "version": version}


def instance_label(hostname: str, port: int) -> str:
    """Single DNS label for this process.

    Hostname alone collides when two PortableAI processes share a machine
    (different ports). Dots would split labels, so they become dashes.
    """
    safe = "-".join(part for part in hostname.replace(".", "-").split("-") if part)
    return f"{safe or 'portableai'}-{int(port)}"


def service_instance_name(hostname: str, port: int) -> str:
    return f"{instance_label(hostname, port)}.{SERVICE_TYPE}"


def _usable_ipv4(ip: str) -> bool:
    if not ip or ip.startswith("127."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _skip_iface_name(name: str) -> bool:
    lowered = (name or "").lower()
    return any(lowered.startswith(prefix) for prefix in _SKIP_IFACE_PREFIXES)


def lan_ipv4_addresses() -> list[str]:
    """Non-loopback IPv4 addresses via ifaddr (zeroconf's dependency).

    Same call on Linux and macOS. Loopback is omitted so a phone is not
    handed 127.0.0.1. If nothing usable is found, 127.0.0.1 is the last
    resort so the ServiceInfo still has an A record for local browse.
    """
    seen: list[str] = []
    try:
        import ifaddr
    except ImportError:
        return ["127.0.0.1"]

    for adapter in ifaddr.get_adapters():
        name = adapter.nice_name or adapter.name or ""
        if _skip_iface_name(name):
            continue
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            addr = ip.ip
            if isinstance(addr, str) and _usable_ipv4(addr) and addr not in seen:
                seen.append(addr)
    return seen or ["127.0.0.1"]


def build_service_info(
    *,
    hostname: str,
    port: int,
    version: str,
    addresses: list[str] | None = None,
):
    from zeroconf import ServiceInfo

    ips = addresses if addresses is not None else lan_ipv4_addresses()
    packed = [socket.inet_aton(ip) for ip in ips]
    server = f"{instance_label(hostname, port)}.local."
    return ServiceInfo(
        SERVICE_TYPE,
        service_instance_name(hostname, port),
        addresses=packed,
        port=int(port),
        properties=txt_records(hostname, version),
        server=server,
    )


class MdnsAdvertiser:
    """Register ``_portableai._tcp`` and unregister on the same stop path."""

    def __init__(self, zeroconf_factory: Callable | None = None) -> None:
        self._zeroconf_factory = zeroconf_factory
        self._zc = None
        self._info = None

    @property
    def info(self):
        return self._info

    def start(
        self,
        port: int,
        *,
        hostname: str | None = None,
        version: str,
        addresses: list[str] | None = None,
    ) -> bool:
        """Advertise on ``port``. Returns False if registration failed.

        Failure is non-fatal: Flask/Ollama should still start. A missing
        broadcast is better than crashing the chat UI.
        """
        self.stop()
        host = hostname if hostname is not None else socket.gethostname()
        try:
            from zeroconf import Zeroconf

            factory = self._zeroconf_factory or Zeroconf
            info = build_service_info(
                hostname=host,
                port=port,
                version=version,
                addresses=addresses,
            )
            zc = factory()
            zc.register_service(info)
        except Exception as exc:
            print(
                f"WARNING: mDNS/Bonjour advertise failed ({exc}). "
                "Phones will need the LAN IP or QR code to connect.",
                file=sys.stderr,
                flush=True,
            )
            return False

        self._zc = zc
        self._info = info
        print(
            f"Advertising as {host} via mDNS ({SERVICE_TYPE.rstrip('.')}) "
            f"on port {port}",
            flush=True,
        )
        return True

    def stop(self) -> None:
        """Unregister and close. Idempotent; never raises."""
        zc, info = self._zc, self._info
        self._zc = None
        self._info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
        except Exception as exc:
            print(f"WARNING: mDNS unregister failed ({exc})", file=sys.stderr, flush=True)
        try:
            zc.close()
        except Exception:
            pass
