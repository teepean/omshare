"""End-to-end tests of the download path against a mock camera (no hardware)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import mock_camera  # noqa: E402

from olympuswifi.camera import OlympusCamera  # noqa: E402
from omshare import download as dl  # noqa: E402


@pytest.fixture
def camera():
    httpd = mock_camera.serve()
    port = httpd.server_address[1]
    OlympusCamera.URL_PREFIX = f"http://127.0.0.1:{port}/"
    OlympusCamera.HEADERS = {"Host": f"127.0.0.1:{port}",
                             "User-Agent": "OI.Share v2"}
    try:
        yield OlympusCamera()
    finally:
        httpd.shutdown()


def test_model_and_listing(camera):
    assert camera.get_camera_model() == "E-M10MarkII"
    plan = dl.build_plan(camera)
    assert len(plan.files) == 2
    assert plan.total_bytes == 120000 + 15000000
    # FAT date/time decode is correct.
    assert plan.files[0].date_time == "2026-06-05T12:34:56"


def test_extension_filter(camera):
    plan = dl.build_plan(camera, extensions=["orf"])
    assert [f.file_name for f in plan.files] == ["/DCIM/100OLYMP/P1010043.ORF"]


def test_download_and_resume(camera, tmp_path):
    plan = dl.build_plan(camera)
    s1 = dl.download(camera, plan, str(tmp_path), organize="date", log=lambda *_: None)
    assert s1 == {"downloaded": 2, "skipped": 0, "failed": 0,
                  "bytes": 15120000}
    # Files exist with the right sizes in date-organized folders.
    jpg = tmp_path / "2026-06-05" / "P1010042.JPG"
    orf = tmp_path / "2026-06-05" / "P1010043.ORF"
    assert jpg.stat().st_size == 120000
    assert orf.stat().st_size == 15000000
    # Second run skips everything (resume).
    s2 = dl.download(camera, plan, str(tmp_path), organize="date", log=lambda *_: None)
    assert s2["skipped"] == 2 and s2["downloaded"] == 0


def test_dry_run_writes_nothing(camera, tmp_path):
    plan = dl.build_plan(camera)
    dl.download(camera, plan, str(tmp_path), dry_run=True, log=lambda *_: None)
    assert not any(tmp_path.iterdir())
