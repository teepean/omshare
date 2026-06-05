"""
Photo/video download from an Olympus camera, built on olympuswifi.camera.

Adds, on top of the base library: resume (skip already-downloaded files),
date-organized output folders, extension/date filters, and a progress summary.
"""

import datetime
import os
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from olympuswifi.camera import OlympusCamera, ResultError


@dataclass
class Plan:
    files: List["OlympusCamera.FileDescr"]
    total_bytes: int


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _matches(fd, extensions: Optional[List[str]],
             daterange: Tuple[Optional[datetime.date], Optional[datetime.date]]) -> bool:
    if extensions:
        if not any(fd.file_name.lower().endswith("." + e.lower().lstrip("."))
                   for e in extensions):
            return False
    if daterange[0] and daterange[1]:
        d = datetime.datetime.fromisoformat(fd.date_time).date()
        if not (daterange[0] <= d <= daterange[1]):
            return False
    return True


def build_plan(
    camera: OlympusCamera,
    extensions: Optional[List[str]] = None,
    daterange: Tuple[Optional[datetime.date], Optional[datetime.date]] = (None, None),
) -> Plan:
    """List camera files (recursively) and filter them."""
    files = [f for f in camera.list_images() if _matches(f, extensions, daterange)]
    files.sort(key=lambda f: f.date_time)
    return Plan(files, sum(f.file_size for f in files))


def _local_path(fd, output_dir: str, organize: str) -> str:
    name = fd.file_name.split("/")[-1]
    if organize == "flat":
        sub = ""
    elif organize == "date":          # YYYY-MM-DD/
        sub = fd.date_time[:10]
    elif organize == "year":          # YYYY/
        sub = fd.date_time[:4]
    elif organize == "mirror":        # mirror DCIM/100OLYMP/ structure
        sub = fd.file_name.lstrip("/").rsplit("/", 1)[0]
    else:
        sub = ""
    return os.path.join(output_dir, sub, name)


def _already_have(local_file: str, fd) -> bool:
    if not os.path.exists(local_file):
        return False
    st = os.stat(local_file)
    if st.st_size != fd.file_size:
        return False
    dt = datetime.datetime.strptime(fd.date_time, "%Y-%m-%dT%H:%M:%S")
    return abs(dt.timestamp() - st.st_mtime) < 10


def download(
    camera: OlympusCamera,
    plan: Plan,
    output_dir: str,
    organize: str = "date",
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """
    Download the files in `plan` into `output_dir`. Returns a stats dict.
    `organize`: 'date' | 'year' | 'flat' | 'mirror'.
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0}
    n = len(plan.files)
    width = len(str(n))

    for i, fd in enumerate(plan.files, 1):
        local_file = _local_path(fd, output_dir, organize)
        rel = local_file.replace(os.path.expanduser("~"), "~")
        prefix = f"[{i:>{width}}/{n}]"

        if _already_have(local_file, fd):
            stats["skipped"] += 1
            log(f"{prefix} skip   {rel}  (already downloaded)")
            continue

        if dry_run:
            stats["downloaded"] += 1
            stats["bytes"] += fd.file_size
            log(f"{prefix} would  {rel}  ({_human(fd.file_size)})")
            continue

        os.makedirs(os.path.dirname(local_file) or ".", exist_ok=True)
        try:
            data = camera.download_image(fd.file_name)
            if data is None or len(data) != fd.file_size:
                raise IOError(
                    f"size mismatch (got {0 if data is None else len(data)}, "
                    f"expected {fd.file_size})"
                )
            tmp = local_file + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, local_file)
            dt = datetime.datetime.strptime(fd.date_time, "%Y-%m-%dT%H:%M:%S")
            os.utime(local_file, (dt.timestamp(), dt.timestamp()))
            stats["downloaded"] += 1
            stats["bytes"] += fd.file_size
            log(f"{prefix} get    {rel}  ({_human(fd.file_size)})")
        except (ResultError, IOError, OSError) as e:
            stats["failed"] += 1
            log(f"{prefix} FAIL   {fd.file_name}: {e}")
            try:
                os.remove(local_file + ".part")
            except OSError:
                pass

    return stats
