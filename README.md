# omshare — OLYMPUS Image Share for Linux, macOS & Windows

A **GUI and command-line** replacement for **OLYMPUS Image Share (OI.Share)**
that runs on **Linux, macOS (incl. Apple Silicon M1–M5), and Windows**, aimed at
the thing that matters most when the phone app stops working: **getting your
photos and videos off the camera over WiFi.**

- **GUI** — a thumbnail browser: connect, browse the card, filter by format
  (JPEG / RAW / Video), pick photos (or “Download all”), watch progress.
- **CLI** — scriptable: `omshare download`, with filters, resume, set-clock,
  remote shutter, power-off.

WiFi is handled per-platform automatically: **`nmcli` on Linux**,
**`networksetup` on macOS**, **`netsh wlan` on Windows**. Everything else (the
HTTP/CGI protocol, download logic, GUI) is pure Python and identical on all three.

Tested protocol target: **Olympus OM-D E-M10 Mark II** (works with most OM-D /
PEN / TG cameras, since it queries the camera for its own command list). Both
**JPEG and ORF (RAW)** files — including RAW+JPEG pairs — are listed and
downloaded; RAW files show a camera-generated JPEG thumbnail in the GUI.

## How it works

An Olympus camera in WiFi mode is a **WiFi access point** at the fixed address
`192.168.0.10` running a plain HTTP/CGI server — no encryption, no login token.
This tool:

1. Joins the camera's WiFi access point using the OS's native tooling
   (`nmcli` / `networksetup` / `netsh`), trying not to hijack your default route
   so your wired/other internet keeps working.
2. Speaks the camera's HTTP/CGI protocol (via the MIT-licensed
   [`olympuswifi`](https://github.com/joergmlpts/olympus-wifi) library) to list
   and download files.

> The protocol was confirmed by decompiling OI.Share 4.6.0 and cross-checking
> against the published Olympus OPC protocol spec and community reimplementations.

## ⚠️ Requirement: a WiFi adapter

Because the camera *is* the access point, the computer running this tool needs a
**WiFi interface** to join it (2.4 GHz / WPA2 is all the camera offers).

- **macOS:** any Mac with built-in Wi-Fi works out of the box (MacBooks, iMac,
  Mac mini, Mac Studio, Mac Pro). Check with `networksetup -listallhardwareports`.
- **Windows:** any laptop/desktop with a Wi-Fi adapter. Check with
  `netsh wlan show interfaces`.
- **Linux:** a desktop with only ethernet won't work — use a laptop with WiFi or
  a cheap USB WiFi adapter. Check with `nmcli device status`.

## Install

```bash
git clone https://github.com/teepean/omshare
cd omshare
python3 -m venv .venv
. .venv/bin/activate
pip install ".[gui]"     # GUI + CLI  (use 'pip install .' for CLI only)
```

This pulls in `olympuswifi` + `requests` (and `PySide6` for the GUI) from PyPI.
The protocol layer is the MIT-licensed
[`olympuswifi`](https://github.com/joergmlpts/olympus-wifi) package.

## GUI

```bash
omshare-gui
```

Enter the camera's WiFi SSID + password (shown on the camera) and click
**Connect** — the grid fills with thumbnails. Use the **Show: JPEG / RAW / Video**
checkboxes to filter, select photos (or **Download all**), and pick a destination
folder. Already-downloaded files are skipped, so you can re-run anytime.

### Build a double-clickable app

You chose "both app + command", so there are build scripts (run them on the
target OS — a `.app`/`.exe` can only be built on that OS):

```bash
./packaging/build-macos-app.sh      # macOS  -> dist/omshare.app
.\packaging\build-windows.ps1       # Windows -> dist\omshare\omshare.exe
```

On macOS, the first `omshare connect` may pop a system prompt asking permission
to change network settings / use the keychain — approve it. The wired connection
(if any) stays primary for internet as long as it's above Wi-Fi in **System
Settings → Network → ⋯ → Set Service Order**; the tool warns you if it isn't.

## Connect the camera (E-M10 Mark II)

1. On the camera: **Menu → Connection to Smartphone** (the WiFi icon). The
   screen shows an **SSID** (e.g. `E-M10II-XXXXXX`) and a **password**.
   (Tip: *Wi-Fi Connect Settings → Connection Password* can be set to a fixed
   private password so it doesn't change every time.)
2. On Linux:

```bash
omshare connect --ssid "E-M10II-XXXXXX" --password "XXXXXXXX"
omshare status        # should say: Camera at 192.168.0.10: REACHABLE
```

The camera drops WiFi to standby after ~1 minute idle — if `status` says not
reachable, re-enable WiFi on the camera and reconnect.

## Download

```bash
# Everything new into ~/Pictures/Olympus, in YYYY-MM-DD folders, resume-safe:
omshare download

# Only JPEGs from the last 3 days, to a chosen folder:
omshare download -o ~/trip -e jpg --since 3

# RAW + JPEG in a specific date range, then power the camera off:
omshare download -e orf,jpg -D 2026-06-01 2026-06-05 --power-off

# Preview without writing anything:
omshare download --dry-run
```

One-shot connect + download + disconnect:

```bash
omshare sync --ssid "E-M10II-XXXXXX" --password "XXXXXXXX" -e jpg --since 1
```

## All commands

| Command | What it does |
|---|---|
| `connect --ssid S --password P` | Join the camera's WiFi (internet-safe routing) |
| `disconnect` | Leave the camera WiFi |
| `status` | Show WiFi adapters + whether the camera is reachable |
| `info` | Camera model, version, supported features, command list |
| `list` | List photos/videos on the card (with `--ext` / `--since` / `-D`) |
| `download` | Download files (resume-safe). See flags below. |
| `sync` | `connect` + `download` (+ optional `--keep` / `--power-off`) |
| `shutter` | Take a single picture |
| `set-clock` | Set the camera clock to this computer's time |
| `power-off` | Turn the camera off |

**Download/sync flags:** `-o/--output`, `--organize {date,year,flat,mirror}`,
`-e/--ext`, `--since DAYS`, `-D/--date-range START END`, `--dry-run`,
`--set-clock`, `--power-off`.

Filenames already present locally (matching size + timestamp) are skipped, so
re-running only fetches new shots.

## Notes / limits

- **Live view** and **geotagging** aren't wrapped by this CLI yet, but the
  underlying `olympuswifi` library supports them (`olympus-liveview`,
  `olympus-log2gpx`).
- The two hardware-dependent steps — the actual WiFi join and the real camera's
  HTTP responses — can only be verified against a real camera. Everything else
  is covered by the offline test suite.

## Development & tests

The test suite runs entirely offline against a built-in mock camera (no
hardware, no WiFi needed):

```bash
pip install -e . pytest
pytest -q
```

For manual testing you can point the CLI at the mock server:

```bash
python tests/mock_camera.py &           # serves on 127.0.0.1:8910
OMSHARE_HOST=127.0.0.1:8910 omshare info
OMSHARE_HOST=127.0.0.1:8910 omshare download -o /tmp/out
```

## Credits & license

MIT — see [LICENSE](LICENSE).

Built on the MIT-licensed [`olympuswifi`](https://github.com/joergmlpts/olympus-wifi)
library, which implements the Olympus WiFi protocol. Protocol details cross-checked
against the published Olympus OPC Communication Protocol specification.
