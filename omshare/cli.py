"""
omshare — a Linux command-line replacement for OLYMPUS Image Share (OI.Share),
focused on downloading photos/videos from Olympus / OM-D / PEN / TG cameras
over WiFi.

The camera is a WiFi access point at 192.168.0.10 speaking a plain HTTP/CGI
protocol. This tool joins that access point (NetworkManager), then uses the
olympuswifi library to browse and download the memory card.

Subcommands:
  connect      join the camera's WiFi access point
  disconnect   leave it (and restore default routing)
  status       show WiFi adapter + whether the camera is reachable
  info         connect to the camera and report model / capabilities
  list         list photos/videos on the card (with filters)
  download     download photos/videos (resume-safe)
  sync         connect + download + (optionally) disconnect / power off
  shutter      take a single picture
  set-clock    set the camera clock to this computer's time
  power-off    turn the camera off
"""

import argparse
import datetime
import getpass
import os
import sys
from typing import List, Optional, Tuple

from . import wifi
from . import download as dl

# Camera host; overridable for testing / unusual setups. Default is the fixed
# address every Olympus camera uses in WiFi access-point mode.
_HOST = os.environ.get("OMSHARE_HOST", wifi.CAMERA_HOST)


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _err(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _apply_host(host: str) -> None:
    """Point this process (reachability check + library) at `host`."""
    global _HOST
    _HOST = host
    from olympuswifi.camera import OlympusCamera
    OlympusCamera.URL_PREFIX = f"http://{host}/"
    OlympusCamera.HEADERS = {"Host": host, "User-Agent": "OI.Share v2"}


def _connect_camera():
    """Construct an OlympusCamera, with a friendly error if unreachable."""
    host, port = (_HOST.split(":") + ["80"])[:2]
    if not wifi.is_camera_reachable(host, int(port)):
        _err(
            f"camera not reachable at {_HOST}. Connect first with "
            "'omshare connect --ssid <SSID> --password <PW>', or check that "
            "WiFi is enabled on the camera."
        )
    # Import here so 'connect'/'status' work even if requests isn't needed yet.
    from olympuswifi.camera import OlympusCamera
    try:
        return OlympusCamera()
    except Exception as e:  # noqa: BLE001 - surface any connection failure clearly
        _err(f"failed to talk to camera: {e}")


def _parse_date(s: str) -> datetime.date:
    """A YYYY-MM-DD date, or an integer = days-before-today (0 = today)."""
    try:
        return datetime.date.today() - datetime.timedelta(days=int(s))
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date '{s}': use YYYY-MM-DD or an integer day offset"
        )


def _daterange_from_args(args) -> Tuple[Optional[datetime.date], Optional[datetime.date]]:
    if getattr(args, "since", None) is not None:
        return (datetime.date.today() - datetime.timedelta(days=args.since),
                datetime.date.today())
    if getattr(args, "date_range", None):
        start, end = args.date_range
        if start > end:
            _err("start date must be on or before end date")
        return (start, end)
    return (None, None)


def _extensions(args) -> Optional[List[str]]:
    if not getattr(args, "ext", None):
        return None
    out: List[str] = []
    for chunk in args.ext:
        out.extend(e for e in chunk.split(",") if e)
    return out or None


# --------------------------------------------------------------------------- #
#  Subcommands
# --------------------------------------------------------------------------- #

def cmd_connect(args) -> None:
    password = args.password
    if password is None:
        password = getpass.getpass(f"WiFi password for '{args.ssid}': ")
    print(f"Joining camera WiFi '{args.ssid}' ...")
    con = wifi.connect(args.ssid, password, iface=args.iface)
    print(f"Connected ({con}). Camera reachable at {wifi.CAMERA_HOST}.")
    print("Your other network connection remains the default route for internet.")


def cmd_disconnect(args) -> None:
    wifi.disconnect(args.ssid)
    print("Disconnected from camera WiFi.")


def cmd_status(args) -> None:
    try:
        devs = wifi.list_wifi_devices()
        if devs:
            print("WiFi adapters:")
            for d in devs:
                print(f"  {d.device:12} {d.state:14} {d.connection}")
        else:
            print("WiFi adapters: none found (a WiFi adapter is required to "
                  "reach the camera).")
    except wifi.WifiError as e:
        print(f"WiFi: {e}")
    host, port = (_HOST.split(":") + ["80"])[:2]
    reachable = wifi.is_camera_reachable(host, int(port))
    print(f"Camera at {_HOST}: "
          f"{'REACHABLE' if reachable else 'not reachable'}")
    if reachable:
        from olympuswifi.camera import OlympusCamera
        try:
            cam = OlympusCamera()
            print(f"Model: {cam.get_camera_model()}")
        except Exception as e:  # noqa: BLE001
            print(f"(could not query model: {e})")


def cmd_info(args) -> None:
    cam = _connect_camera()
    cam.report_model()
    print("\nSupported feature flags:")
    print("  " + ", ".join(sorted(cam.get_supported())) or "  (none)")
    print("\nAvailable commands:")
    for name in sorted(cam.get_commands()):
        print(f"  {name}")


def cmd_list(args) -> None:
    cam = _connect_camera()
    plan = dl.build_plan(cam, _extensions(args), _daterange_from_args(args))
    for fd in plan.files:
        print(f"{fd.date_time}  {fd.file_size:>12,}  {fd.file_name}")
    print(f"\n{len(plan.files)} file(s), {dl._human(plan.total_bytes)} total.")


def cmd_download(args) -> None:
    cam = _connect_camera()
    if args.set_clock:
        cam.set_clock()
        print("Camera clock set to this computer's time.")
    plan = dl.build_plan(cam, _extensions(args), _daterange_from_args(args))
    if not plan.files:
        print("No matching files on the camera.")
    else:
        print(f"{len(plan.files)} file(s) to consider, "
              f"{dl._human(plan.total_bytes)} total. -> {args.output}")
        stats = dl.download(cam, plan, args.output, organize=args.organize,
                            dry_run=args.dry_run)
        print(f"\nDone: {stats['downloaded']} downloaded "
              f"({dl._human(stats['bytes'])}), {stats['skipped']} skipped, "
              f"{stats['failed']} failed.")
    if args.power_off:
        cam.send_command("exec_pwoff")
        print("Camera powering off.")


def cmd_sync(args) -> None:
    """connect (if creds given) -> download -> optional disconnect/power-off."""
    connected_here = False
    if args.ssid:
        password = args.password or getpass.getpass(
            f"WiFi password for '{args.ssid}': ")
        print(f"Joining camera WiFi '{args.ssid}' ...")
        wifi.connect(args.ssid, password, iface=args.iface)
        connected_here = True
        print("Connected.")
    try:
        cmd_download(args)
    finally:
        if args.power_off:
            pass  # already handled in cmd_download
        if connected_here and not args.keep:
            wifi.disconnect(args.ssid)
            print("Disconnected from camera WiFi.")


def cmd_shutter(args) -> None:
    cam = _connect_camera()
    print("Taking a picture ...")
    cam.take_picture()
    print("Done.")


def cmd_set_clock(args) -> None:
    cam = _connect_camera()
    cam.set_clock()
    print("Camera clock set to this computer's time.")


def cmd_power_off(args) -> None:
    cam = _connect_camera()
    cam.send_command("exec_pwoff")
    print("Camera powering off.")


# --------------------------------------------------------------------------- #
#  Argument parser
# --------------------------------------------------------------------------- #

def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ext", "-e", action="append", metavar="EXT",
                   help="only this extension; repeatable or comma-separated, "
                        "e.g. -e jpg,orf")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--since", type=int, metavar="DAYS",
                   help="only files from the last DAYS days (0 = today)")
    g.add_argument("--date-range", "-D", nargs=2, type=_parse_date,
                   metavar=("START", "END"),
                   help="date range; YYYY-MM-DD or integer day-offsets")


def _add_download_args(p: argparse.ArgumentParser) -> None:
    import os
    default_out = os.path.join(os.path.expanduser("~"), "Pictures", "Olympus")
    p.add_argument("--output", "-o", default=default_out,
                   help=f"output directory (default: {default_out})")
    p.add_argument("--organize", choices=["date", "year", "flat", "mirror"],
                   default="date",
                   help="folder layout (default: date = YYYY-MM-DD subfolders)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be downloaded without writing files")
    p.add_argument("--set-clock", action="store_true",
                   help="set the camera clock before downloading")
    p.add_argument("--power-off", action="store_true",
                   help="turn the camera off when finished")
    _add_filter_args(p)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omshare",
        description="Linux command-line OLYMPUS Image Share (download-focused).",
    )
    parser.add_argument("--host", default=None,
                        help="camera address (default 192.168.0.10; or set "
                             "OMSHARE_HOST). Mainly for testing.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("connect", help="join the camera's WiFi access point")
    p.add_argument("--ssid", "-s", required=True, help="camera WiFi SSID")
    p.add_argument("--password", "-p", help="WiFi password (prompted if omitted)")
    p.add_argument("--iface", "-i", help="WiFi interface to use")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("disconnect", help="leave the camera WiFi")
    p.add_argument("--ssid", "-s", help="only disconnect this SSID")
    p.set_defaults(func=cmd_disconnect)

    p = sub.add_parser("status", help="show WiFi + camera reachability")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("info", help="report camera model and capabilities")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", help="list photos/videos on the card")
    _add_filter_args(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("download", help="download photos/videos")
    _add_download_args(p)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("sync", help="connect + download in one step")
    p.add_argument("--ssid", "-s", help="camera WiFi SSID (to connect first)")
    p.add_argument("--password", "-p", help="WiFi password (prompted if omitted)")
    p.add_argument("--iface", "-i", help="WiFi interface to use")
    p.add_argument("--keep", action="store_true",
                   help="stay connected to the camera WiFi afterwards")
    _add_download_args(p)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("shutter", help="take a single picture")
    p.set_defaults(func=cmd_shutter)

    p = sub.add_parser("set-clock", help="set camera clock to this computer's time")
    p.set_defaults(func=cmd_set_clock)

    p = sub.add_parser("power-off", help="turn the camera off")
    p.set_defaults(func=cmd_power_off)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "host", None):
        _apply_host(args.host)
    elif _HOST != wifi.CAMERA_HOST:   # came from OMSHARE_HOST env var
        _apply_host(_HOST)
    try:
        args.func(args)
    except wifi.WifiError as e:
        _err(str(e))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
