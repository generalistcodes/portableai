"""mDNS / Bonjour advertisement for discovering which host is running."""
from __future__ import annotations

import os
import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdns_broadcast import (
    SERVICE_TYPE,
    MdnsAdvertiser,
    build_service_info,
    instance_label,
    lan_ipv4_addresses,
    service_instance_name,
    txt_records,
)

ROOT = Path(__file__).resolve().parent.parent


def _server_app_version() -> str:
    text = (ROOT / "ui" / "server.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"', text, re.M)
    assert match, "APP_VERSION missing in ui/server.py"
    return match.group(1)


APP_VERSION = _server_app_version()


class FakeZeroconf:
    def __init__(self) -> None:
        self.registered = []
        self.unregistered = []
        self.closed = False

    def register_service(self, info) -> None:
        self.registered.append(info)

    def unregister_service(self, info) -> None:
        self.unregistered.append(info)

    def close(self) -> None:
        self.closed = True


def test_service_type_is_portableai_tcp_local():
    assert SERVICE_TYPE == "_portableai._tcp.local."


def test_txt_version_matches_server_app_version():
    text = (ROOT / "ui" / "server.py").read_text(encoding="utf-8")
    assert f'APP_VERSION = "{APP_VERSION}"' in text


def test_txt_records_include_hostname_and_version():
    txt = txt_records("gg-G5-KC", APP_VERSION)
    assert txt == {"name": "gg-G5-KC", "version": APP_VERSION}


def test_txt_records_distinguish_ubuntu_from_mac_hostnames():
    ubuntu = txt_records("gg-G5-KC", APP_VERSION)
    mac = txt_records("Kims-MacBook-Air", APP_VERSION)
    assert ubuntu["name"] != mac["name"]
    assert ubuntu["version"] == mac["version"] == APP_VERSION


def test_two_ports_on_one_host_get_distinct_instance_names():
    a = service_instance_name("gg-G5-KC", 5050)
    b = service_instance_name("gg-G5-KC", 5052)
    assert a != b
    assert a.endswith(SERVICE_TYPE)
    assert str(5050) in instance_label("gg-G5-KC", 5050)
    assert str(5052) in instance_label("gg-G5-KC", 5052)


def test_build_service_info_advertises_type_port_and_txt():
    info = build_service_info(
        hostname="gg-G5-KC",
        port=5050,
        version=APP_VERSION,
        addresses=["192.168.1.134"],
    )
    assert info.type == SERVICE_TYPE
    assert info.port == 5050
    assert info.decoded_properties == {"name": "gg-G5-KC", "version": APP_VERSION}
    assert info.parsed_addresses() == ["192.168.1.134"]


def test_build_service_info_mac_hostname_in_txt():
    info = build_service_info(
        hostname="Kims-MacBook-Air",
        port=5050,
        version=APP_VERSION,
        addresses=["192.168.1.20"],
    )
    assert info.decoded_properties["name"] == "Kims-MacBook-Air"
    assert info.name.startswith("Kims-MacBook-Air-5050.")


def test_start_registers_service_with_injected_zeroconf():
    fake = FakeZeroconf()
    advertiser = MdnsAdvertiser(zeroconf_factory=lambda: fake)
    assert advertiser.start(
        5050,
        hostname="gg-G5-KC",
        version=APP_VERSION,
        addresses=["192.168.1.134"],
    )
    assert len(fake.registered) == 1
    info = fake.registered[0]
    assert info.type == SERVICE_TYPE
    assert info.port == 5050
    assert info.decoded_properties == {"name": "gg-G5-KC", "version": APP_VERSION}


def test_stop_unregisters_and_closes():
    fake = FakeZeroconf()
    advertiser = MdnsAdvertiser(zeroconf_factory=lambda: fake)
    advertiser.start(5050, hostname="gg-G5-KC", version=APP_VERSION, addresses=["127.0.0.1"])
    info = fake.registered[0]
    advertiser.stop()
    assert fake.unregistered == [info]
    assert fake.closed is True
    assert advertiser.info is None


def test_stop_without_start_is_safe():
    MdnsAdvertiser().stop()


def test_stop_is_idempotent():
    fake = FakeZeroconf()
    advertiser = MdnsAdvertiser(zeroconf_factory=lambda: fake)
    advertiser.start(5050, hostname="gg-G5-KC", version=APP_VERSION, addresses=["127.0.0.1"])
    advertiser.stop()
    advertiser.stop()
    assert len(fake.unregistered) == 1


def test_start_failure_does_not_raise():
    class Boom:
        def register_service(self, info):
            raise OSError("no multicast")

    advertiser = MdnsAdvertiser(zeroconf_factory=Boom)
    assert advertiser.start(5050, hostname="gg-G5-KC", version=APP_VERSION, addresses=["127.0.0.1"]) is False
    assert advertiser.info is None


def test_lan_ipv4_addresses_skips_loopback():
    ips = lan_ipv4_addresses()
    assert ips
    if ips != ["127.0.0.1"]:
        assert all(not ip.startswith("127.") for ip in ips)


def test_mdns_module_has_no_os_specific_shellouts():
    src = (ROOT / "src" / "mdns_broadcast.py").read_text(encoding="utf-8")
    assert "sys.platform" not in src
    assert "ifconfig" not in src
    assert "ipconfig" not in src
    assert "hostname -I" not in src


def test_run_py_skips_vendor_when_external_ollama_url_set():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "start_managed_ollama" in text
    assert "PORTABLEAI_EXTERNAL_OLLAMA_URL" in text
    assert 'mode="external"' in text
    assert 'mode="bundled"' in text


def test_run_py_advertises_on_startup_and_unregisters_on_ollama_shutdown_path():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "from mdns_broadcast import MdnsAdvertiser" in text
    assert "advertiser.start(port, version=server.APP_VERSION)" in text
    assert "advertiser.stop()" in text
    cleanup_at = text.index("def _cleanup()")
    stop_mdns_at = text.index("advertiser.stop()", cleanup_at)
    stop_runtime_at = text.index("_stop_runtime()", stop_mdns_at)
    assert stop_mdns_at < stop_runtime_at
    assert "atexit.register(_cleanup)" in text
    assert "_cleanup()" in text.split("def _on_sigterm")[1]
    assert "finally:" in text
    finally_block = text.split("finally:")[-1]
    assert "_cleanup()" in finally_block


def test_run_py_ensures_zeroconf_dependency():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert '("zeroconf", "zeroconf")' in text


def _browse(timeout: float = 4.0) -> dict:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    found: dict = {}

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if info is None:
                return
            found[name] = {
                "port": info.port,
                "txt": dict(info.decoded_properties),
            }

        def remove_service(self, zc, type_, name):
            found.pop(name, None)

        def update_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    browser = ServiceBrowser(zc, SERVICE_TYPE, Listener())
    deadline = time.time() + timeout
    snapshot = dict(found)
    try:
        while time.time() < deadline:
            snapshot = dict(found)
            yield snapshot
            time.sleep(0.15)
    finally:
        browser.cancel()
        zc.close()


def _wait_for(predicate, timeout: float = 5.0) -> dict:
    gen = _browse(timeout=timeout)
    last = {}
    try:
        for snapshot in gen:
            last = snapshot
            if predicate(snapshot):
                return snapshot
        return last
    finally:
        gen.close()


def test_live_browse_finds_two_distinct_named_servers():
    """Real multicast: two distinctly named entries both visible.

    Hostnames are unique per run so a live PortableAI on the LAN (e.g.
    gg-G5-KC:5050) cannot steal the TXT name and fail the port assert.
    """
    suffix = f"{socket.gethostname()}-{os.getpid()}"
    ubuntu_name = f"portableai-test-ubuntu-{suffix}"
    mac_name = f"portableai-test-mac-{suffix}"
    ubuntu = MdnsAdvertiser()
    mac = MdnsAdvertiser()
    try:
        assert ubuntu.start(15901, hostname=ubuntu_name, version=APP_VERSION)
        assert mac.start(15902, hostname=mac_name, version=APP_VERSION)
        found = _wait_for(
            lambda snap: {v["txt"].get("name") for v in snap.values()}
            >= {ubuntu_name, mac_name}
        )
        names = {entry["txt"]["name"]: entry for entry in found.values()}
        assert ubuntu_name in names, found
        assert mac_name in names, found
        assert names[ubuntu_name]["txt"]["version"] == APP_VERSION
        assert names[mac_name]["txt"]["version"] == APP_VERSION
        assert names[ubuntu_name]["port"] == 15901
        assert names[mac_name]["port"] == 15902
    finally:
        ubuntu.stop()
        mac.stop()


def test_live_stop_removes_advertisement():
    advertiser = MdnsAdvertiser()
    hostname = f"portableai-test-{socket.gethostname()}"
    port = 15903
    try:
        assert advertiser.start(port, hostname=hostname, version=APP_VERSION)
        found = _wait_for(lambda snap: any(v["txt"].get("name") == hostname for v in snap.values()))
        assert any(v["txt"].get("name") == hostname for v in found.values()), found
    finally:
        advertiser.stop()
    gone = _wait_for(
        lambda snap: not any(v["txt"].get("name") == hostname for v in snap.values())
    )
    assert not any(v["txt"].get("name") == hostname for v in gone.values()), gone
