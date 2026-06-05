"""
Cross-platform WiFi connection management for Olympus cameras.

An Olympus camera (E-M10 II etc.) acts as a WiFi *access point* at the fixed
address 192.168.0.10 and runs a DHCP server. To talk to it, this computer must
join that access point as a station.

Two backends are provided and selected automatically by platform:

* **Linux**  — NetworkManager (`nmcli`). We create a dedicated connection
  profile with ``ipv4.never-default yes`` so joining the camera (which has no
  internet) does not hijack your default route / kill your wired internet.

* **macOS** (Apple Silicon incl. M-series) — `networksetup`. macOS keeps the
  higher-priority network *service* (usually Ethernet) as the primary route, so
  your wired internet keeps working while WiFi is on the camera. We warn if
  Wi-Fi is ordered above your wired service.

The public API (``pick_wifi_iface``, ``connect``, ``disconnect``,
``list_wifi_devices``, ``is_camera_reachable``, ``scan``) is identical on both.
"""

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

CAMERA_HOST = "192.168.0.10"
CAMERA_PORT = 80
# Prefix for the NetworkManager profiles we create on Linux, so we can find and
# clean up after ourselves without touching the user's other saved networks.
CON_PREFIX = "omshare-"

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform.startswith("win")


class WifiError(Exception):
    """Raised when a WiFi operation fails or no WiFi adapter is present."""


@dataclass
class WifiDevice:
    device: str
    state: str
    connection: str


# --------------------------------------------------------------------------- #
#  Shared helpers
# --------------------------------------------------------------------------- #

def is_camera_reachable(host: str = CAMERA_HOST, port: int = CAMERA_PORT,
                        timeout: float = 2.0) -> bool:
    """True if a TCP connection to the camera's HTTP port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(argv: List[str], check: bool = True, timeout: int = 60,
         tool_hint: str = "") -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise WifiError(
            f"'{argv[0]}' not found. {tool_hint}".strip()
        )
    except subprocess.TimeoutExpired:
        raise WifiError(f"{' '.join(argv)} timed out.")
    if check and cp.returncode != 0:
        raise WifiError(
            f"{' '.join(argv)} failed ({cp.returncode}): "
            f"{cp.stderr.strip() or cp.stdout.strip()}"
        )
    return cp


def _wait_for_camera(ssid: str, wait_secs: int) -> None:
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        if is_camera_reachable():
            return
        time.sleep(1)
    raise WifiError(
        f"Joined '{ssid}' but {CAMERA_HOST} is not responding. The camera may "
        "have dropped to standby (it times out after ~1 min idle) — re-enable "
        "WiFi on the camera and retry."
    )


# =========================================================================== #
#  Linux backend (NetworkManager / nmcli)
# =========================================================================== #

class _LinuxBackend:
    HINT = ("This tool uses NetworkManager to join the camera's WiFi. Install "
            "NetworkManager, or connect to the camera AP manually and use the "
            "other subcommands.")

    def _nmcli(self, *args: str, check: bool = True, timeout: int = 60):
        return _run(["nmcli", *args], check=check, timeout=timeout,
                    tool_hint=self.HINT)

    @staticmethod
    def _split_terse(line: str) -> List[str]:
        # nmcli -t escapes ':' inside fields as '\:'; split on unescaped ':'.
        out, cur, i = [], [], 0
        while i < len(line):
            c = line[i]
            if c == "\\" and i + 1 < len(line):
                cur.append(line[i + 1])
                i += 2
                continue
            if c == ":":
                out.append("".join(cur))
                cur = []
            else:
                cur.append(c)
            i += 1
        out.append("".join(cur))
        return out

    def list_wifi_devices(self) -> List[WifiDevice]:
        cp = self._nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                         "device", "status")
        devices: List[WifiDevice] = []
        for line in cp.stdout.splitlines():
            parts = self._split_terse(line)
            if len(parts) >= 4 and parts[1] == "wifi":
                devices.append(WifiDevice(parts[0], parts[2], parts[3]))
        return devices

    def pick_wifi_iface(self, preferred: Optional[str] = None) -> str:
        devices = self.list_wifi_devices()
        if preferred:
            for d in devices:
                if d.device == preferred:
                    return preferred
            raise WifiError(
                f"WiFi interface '{preferred}' not found. "
                f"Available: {', '.join(d.device for d in devices) or 'none'}.")
        if not devices:
            raise WifiError(_NO_ADAPTER_MSG)
        for d in devices:
            if d.state in ("disconnected", "unavailable"):
                return d.device
        return devices[0].device

    def connect(self, ssid: str, password: str, iface: Optional[str],
                wait_secs: int) -> str:
        iface = self.pick_wifi_iface(iface)
        con = f"{CON_PREFIX}{ssid}"
        existing = self._nmcli("-t", "-f", "NAME", "connection", "show",
                               check=False)
        if con in existing.stdout.splitlines():
            self._nmcli("connection", "delete", con, check=False)
        self._nmcli(
            "connection", "add", "type", "wifi", "ifname", iface,
            "con-name", con, "autoconnect", "no", "ssid", ssid, "--",
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
            # Keep your real internet as the default route:
            "ipv4.never-default", "yes", "ipv6.never-default", "yes",
            "ipv4.route-metric", "1000",
        )
        try:
            self._nmcli("connection", "up", con, timeout=wait_secs + 10)
        except WifiError as e:
            self._nmcli("connection", "delete", con, check=False)
            raise WifiError(
                f"Failed to join '{ssid}'. Check the SSID/password shown on "
                f"the camera and that WiFi is enabled on the camera. ({e})")
        _wait_for_camera(ssid, wait_secs)
        return con

    def disconnect(self, ssid: Optional[str]) -> None:
        names = self._nmcli("-t", "-f", "NAME", "connection",
                            "show").stdout.splitlines()
        targets = ([f"{CON_PREFIX}{ssid}"] if ssid
                   else [n for n in names if n.startswith(CON_PREFIX)])
        for con in targets:
            if con in names:
                self._nmcli("connection", "down", con, check=False)
                self._nmcli("connection", "delete", con, check=False)

    def scan(self, iface: Optional[str]) -> List[str]:
        iface = self.pick_wifi_iface(iface)
        self._nmcli("device", "wifi", "rescan", "ifname", iface, check=False)
        cp = self._nmcli("-t", "-f", "SSID", "device", "wifi", "list",
                         "ifname", iface, check=False)
        return _dedup(cp.stdout.splitlines())


# =========================================================================== #
#  macOS backend (networksetup)
# =========================================================================== #

class _DarwinBackend:
    HINT = "'networksetup' is part of macOS; this should not happen."

    def _ns(self, *args: str, check: bool = True, timeout: int = 60):
        return _run(["networksetup", *args], check=check, timeout=timeout,
                    tool_hint=self.HINT)

    @staticmethod
    def parse_hardware_ports(text: str) -> List[str]:
        """Return Wi-Fi device names (e.g. ['en0']) from -listallhardwareports."""
        devices, is_wifi = [], False
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Hardware Port:"):
                port = line.split(":", 1)[1].strip().lower()
                is_wifi = port in ("wi-fi", "airport")
            elif line.startswith("Device:") and is_wifi:
                devices.append(line.split(":", 1)[1].strip())
                is_wifi = False
        return devices

    def _wifi_device_names(self) -> List[str]:
        cp = self._ns("-listallhardwareports")
        return self.parse_hardware_ports(cp.stdout)

    def _current_ssid(self, iface: str) -> str:
        cp = self._ns("-getairportnetwork", iface, check=False)
        # "Current Wi-Fi Network: <SSID>"  or  "You are not associated ..."
        out = cp.stdout.strip()
        if ":" in out and "not associated" not in out.lower():
            return out.split(":", 1)[1].strip()
        return ""

    def _power_on(self) -> bool:
        cp = self._ns("-getairportpower", self._wifi_device_names()[0],
                      check=False)
        return cp.stdout.strip().lower().endswith("on")

    def list_wifi_devices(self) -> List[WifiDevice]:
        out = []
        for dev in self._wifi_device_names():
            power = self._ns("-getairportpower", dev, check=False).stdout
            on = power.strip().lower().endswith("on")
            ssid = self._current_ssid(dev) if on else ""
            out.append(WifiDevice(dev, "on" if on else "off", ssid or "--"))
        return out

    def pick_wifi_iface(self, preferred: Optional[str] = None) -> str:
        devices = self._wifi_device_names()
        if preferred:
            if preferred in devices:
                return preferred
            raise WifiError(
                f"WiFi interface '{preferred}' not found. "
                f"Available: {', '.join(devices) or 'none'}.")
        if not devices:
            raise WifiError(_NO_ADAPTER_MSG)
        return devices[0]

    def _warn_service_order(self, iface: str) -> None:
        """Best-effort hint if Wi-Fi outranks the wired service for routing."""
        try:
            order = self._ns("-listnetworkserviceorder", check=False).stdout
        except WifiError:
            return
        services = []
        for line in order.splitlines():
            line = line.strip()
            if line.startswith("(") and ")" in line and "Hardware Port" not in line:
                name = line.split(")", 1)[1].strip()
                if name:
                    services.append(name)
        wifi_idx = next((i for i, s in enumerate(services)
                         if "wi-fi" in s.lower() or "airport" in s.lower()), None)
        wired_idx = next((i for i, s in enumerate(services)
                          if "ethernet" in s.lower() or "lan" in s.lower()
                          or "thunderbolt" in s.lower()), None)
        if wifi_idx is not None and wired_idx is not None and wifi_idx < wired_idx:
            print("note: Wi-Fi is above your wired connection in Service Order, "
                  "so it may try to route internet through the camera (which has "
                  "none). If internet drops, reorder in System Settings > Network "
                  "(⋯ > Set Service Order) to put Ethernet first.")

    def connect(self, ssid: str, password: str, iface: Optional[str],
                wait_secs: int) -> str:
        iface = self.pick_wifi_iface(iface)
        self._ns("-setairportpower", iface, "on", check=False)
        time.sleep(1)
        cp = self._ns("-setairportnetwork", iface, ssid, password, check=False)
        msg = (cp.stdout + cp.stderr).strip()
        # networksetup often returns 0 even on failure, printing an error string.
        if any(s in msg for s in ("Failed to join", "Could not find",
                                  "Error", "not be completed")):
            raise WifiError(
                f"Failed to join '{ssid}': {msg or 'unknown error'}. Check the "
                "SSID/password shown on the camera and that WiFi is enabled on "
                "the camera.")
        self._warn_service_order(iface)
        _wait_for_camera(ssid, wait_secs)
        return iface

    def disconnect(self, ssid: Optional[str]) -> None:
        for dev in self._wifi_device_names():
            if ssid:
                self._ns("-removepreferredwirelessnetwork", dev, ssid,
                         check=False)
            # Disassociate: power-cycle the adapter (no clean per-network
            # disconnect exists since the 'airport' tool was removed).
            self._ns("-setairportpower", dev, "off", check=False)
            time.sleep(1)
            self._ns("-setairportpower", dev, "on", check=False)

    def scan(self, iface: Optional[str]) -> List[str]:
        # Modern macOS removed the 'airport -s' scan; networksetup can't scan.
        # Listing nearby networks isn't essential — we connect by known SSID.
        raise WifiError(
            "Scanning is not available on macOS from the command line. Connect "
            "by SSID with: omshare connect --ssid <SSID> --password <PW>.")


# =========================================================================== #
#  Windows backend (netsh wlan)
# =========================================================================== #

def windows_profile_xml(ssid: str, password: str) -> str:
    """Build a WPA2-PSK/AES WLAN profile XML for `netsh wlan add profile`."""
    from xml.sax.saxutils import escape
    s, p = escape(ssid), escape(password)
    return (
        '<?xml version="1.0"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
        f'  <name>{s}</name>\n'
        f'  <SSIDConfig><SSID><name>{s}</name></SSID></SSIDConfig>\n'
        '  <connectionType>ESS</connectionType>\n'
        '  <connectionMode>manual</connectionMode>\n'
        '  <MSM><security>\n'
        '    <authEncryption><authentication>WPA2PSK</authentication>'
        '<encryption>AES</encryption><useOneX>false</useOneX></authEncryption>\n'
        '    <sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
        f'<keyMaterial>{p}</keyMaterial></sharedKey>\n'
        '  </security></MSM>\n'
        '</WLANProfile>\n'
    )


class _WindowsBackend:
    HINT = "'netsh' is part of Windows; this should not happen."

    def _netsh(self, *args: str, check: bool = True, timeout: int = 60):
        return _run(["netsh", *args], check=check, timeout=timeout,
                    tool_hint=self.HINT)

    @staticmethod
    def parse_interfaces(text: str) -> List["WifiDevice"]:
        """Parse `netsh wlan show interfaces` into a WifiDevice list.

        Each interface block has 'Name', 'State', and (when associated) 'SSID'.
        """
        devices: List[WifiDevice] = []
        name, state, ssid = None, "", "--"
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, val = (p.strip() for p in line.split(":", 1))
            kl = key.lower()
            if kl == "name":
                if name is not None:
                    devices.append(WifiDevice(name, state, ssid))
                name, state, ssid = val, "", "--"
            elif kl == "state" and name is not None:
                state = val
            elif kl == "ssid" and name is not None and val:
                ssid = val
        if name is not None:
            devices.append(WifiDevice(name, state, ssid))
        return devices

    def list_wifi_devices(self) -> List[WifiDevice]:
        cp = self._netsh("wlan", "show", "interfaces", check=False)
        return self.parse_interfaces(cp.stdout)

    def pick_wifi_iface(self, preferred: Optional[str] = None) -> str:
        devices = self.list_wifi_devices()
        names = [d.device for d in devices]
        if preferred:
            if preferred in names:
                return preferred
            raise WifiError(
                f"WiFi interface '{preferred}' not found. "
                f"Available: {', '.join(names) or 'none'}.")
        if not names:
            raise WifiError(_NO_ADAPTER_MSG)
        return names[0]

    def connect(self, ssid: str, password: str, iface: Optional[str],
                wait_secs: int) -> str:
        import os
        import tempfile
        iface = self.pick_wifi_iface(iface)
        xml = windows_profile_xml(ssid, password)
        fd, path = tempfile.mkstemp(suffix=".xml", prefix="omshare-wlan-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(xml)
            self._netsh("wlan", "add", "profile", f"filename={path}",
                        f"interface={iface}", "user=current")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        cp = self._netsh("wlan", "connect", f"name={ssid}", f"ssid={ssid}",
                         f"interface={iface}", check=False)
        msg = (cp.stdout + cp.stderr).strip()
        if cp.returncode != 0 or "completed successfully" not in msg.lower():
            # Not fatal yet — fall through to the reachability wait, but surface
            # the message if the camera never answers.
            pass
        _wait_for_camera(ssid, wait_secs)
        return ssid

    def disconnect(self, ssid: Optional[str]) -> None:
        ifaces = [d.device for d in self.list_wifi_devices()]
        for iface in ifaces or [None]:
            args = ["wlan", "disconnect"]
            if iface:
                args.append(f"interface={iface}")
            self._netsh(*args, check=False)
        if ssid:
            self._netsh("wlan", "delete", "profile", f"name={ssid}", check=False)

    def scan(self, iface: Optional[str]) -> List[str]:
        cp = self._netsh("wlan", "show", "networks", check=False)
        out = []
        for line in cp.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("ssid ") and ":" in line:
                out.append(line.split(":", 1)[1].strip())
        return _dedup(out)


# --------------------------------------------------------------------------- #
#  Backend selection + public API (delegates to the active backend)
# --------------------------------------------------------------------------- #

_NO_ADAPTER_MSG = (
    "No WiFi adapter found. The camera is a WiFi access point, so this computer "
    "needs a WiFi interface to connect to it. Use a Mac/laptop with WiFi, or "
    "plug in a USB WiFi adapter, then retry."
)

if IS_MAC:
    _backend = _DarwinBackend()
elif IS_LINUX:
    _backend = _LinuxBackend()
elif IS_WINDOWS:
    _backend = _WindowsBackend()
else:
    _backend = None  # other platforms: only is_camera_reachable() works


def _require_backend():
    if _backend is None:
        raise WifiError(
            f"Automatic WiFi connection is not supported on this platform "
            f"({sys.platform}). Join the camera's WiFi manually, then use the "
            "other subcommands (info/list/download).")
    return _backend


def _dedup(lines: List[str]) -> List[str]:
    seen, out = set(), []
    for s in (l.strip() for l in lines):
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def list_wifi_devices() -> List[WifiDevice]:
    return _require_backend().list_wifi_devices()


def pick_wifi_iface(preferred: Optional[str] = None) -> str:
    return _require_backend().pick_wifi_iface(preferred)


def connect(ssid: str, password: str, iface: Optional[str] = None,
            wait_secs: int = 25) -> str:
    """
    Join the camera's WiFi access point. Returns a backend-specific handle
    (nmcli profile name on Linux, interface name on macOS). Blocks until the
    camera is reachable or `wait_secs` elapses.
    """
    return _require_backend().connect(ssid, password, iface, wait_secs)


def disconnect(ssid: Optional[str] = None) -> None:
    """Leave the camera WiFi (and clean up any profile we created)."""
    _require_backend().disconnect(ssid)


def scan(iface: Optional[str] = None) -> List[str]:
    """Return a list of nearby SSIDs (Linux only)."""
    return _require_backend().scan(iface)
