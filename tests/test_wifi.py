"""Unit tests for the platform-dispatched WiFi backends (no real hardware)."""

import types

from omshare import wifi as w


def test_linux_terse_split():
    # nmcli -t escapes ':' inside fields as '\:'.
    line = r"en0:wifi:disconnected:My\:Net"
    assert w._LinuxBackend._split_terse(line) == ["en0", "wifi",
                                                   "disconnected", "My:Net"]


def test_macos_hardware_port_parser():
    sample = (
        "Hardware Port: Ethernet\nDevice: en0\n\n"
        "Hardware Port: Wi-Fi\nDevice: en1\n\n"
        "Hardware Port: Thunderbolt Bridge\nDevice: bridge0\n"
    )
    assert w._DarwinBackend.parse_hardware_ports(sample) == ["en1"]
    # Legacy naming on older macOS.
    assert w._DarwinBackend.parse_hardware_ports(
        "Hardware Port: AirPort\nDevice: en0\n") == ["en0"]


def test_macos_connect_builds_command_and_handles_failure(monkeypatch):
    backend = w._DarwinBackend()
    calls = []

    def fake_run(argv, check=True, timeout=60, tool_hint=""):
        calls.append(argv)
        out = ""
        if argv[:2] == ["networksetup", "-listallhardwareports"]:
            out = "Hardware Port: Wi-Fi\nDevice: en1\n"
        elif argv[:2] == ["networksetup", "-listnetworkserviceorder"]:
            out = "(1) Ethernet\n(2) Wi-Fi\n"
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(w, "_run", fake_run)
    monkeypatch.setattr(w, "is_camera_reachable", lambda *a, **k: True)

    handle = backend.connect("E-M10II-AB12", "pw", iface=None, wait_secs=3)
    assert handle == "en1"
    assert ["networksetup", "-setairportnetwork", "en1",
            "E-M10II-AB12", "pw"] in calls

    # networksetup notoriously returns exit 0 even when a join fails.
    def fail_run(argv, check=True, timeout=60, tool_hint=""):
        if argv[:2] == ["networksetup", "-listallhardwareports"]:
            return types.SimpleNamespace(returncode=0,
                stdout="Hardware Port: Wi-Fi\nDevice: en1\n", stderr="")
        if argv[:2] == ["networksetup", "-setairportnetwork"]:
            return types.SimpleNamespace(returncode=0,
                stdout="Failed to join network E-M10II-AB12", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(w, "_run", fail_run)
    try:
        backend.connect("E-M10II-AB12", "wrong", iface=None, wait_secs=2)
        assert False, "expected WifiError"
    except w.WifiError as e:
        assert "Failed to join" in str(e)
