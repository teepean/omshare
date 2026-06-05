"""
Headless end-to-end test of the PySide6 GUI against the mock camera.

Runs entirely offscreen (no display, no real camera) by setting
QT_QPA_PLATFORM=offscreen before Qt is imported. Skipped if PySide6 isn't
installed (it's an optional 'gui' extra).
"""

import os
import sys
import tempfile
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

sys.path.insert(0, os.path.dirname(__file__))
import mock_camera  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _pump(app, cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_gui_connect_browse_download(tmp_path):
    httpd = mock_camera.serve()
    port = httpd.server_address[1]
    os.environ["OMSHARE_HOST"] = f"127.0.0.1:{port}"
    try:
        # Import after OMSHARE_HOST is set so the window picks it up.
        from omshare.gui import MainWindow

        app = QApplication.instance() or QApplication([])
        win = MainWindow()

        # Connect (uses OMSHARE_HOST -> mock camera).
        win._toggle_connect()
        assert _pump(app, lambda: win._connected), "GUI failed to connect"
        assert "E-M10MarkII" in win.status_text.text()

        # Thumbnails populate the grid, with decodable icons.
        assert _pump(app, lambda: win.grid.count() == 2), "thumbnails not loaded"
        names = {win.grid.item(i).data(Qt.UserRole) for i in range(2)}
        assert names == {"/DCIM/100OLYMP/P1010042.JPG",
                         "/DCIM/100OLYMP/P1010043.ORF"}
        assert not win.grid.item(0).icon().isNull(), "thumbnail icon not decoded"
        # ORF (RAW) also gets a camera-generated JPEG thumbnail.
        assert not win.grid.item(1).icon().isNull(), "RAW thumbnail not decoded"

        # Format filter: hide RAW -> only the JPEG remains visible.
        win.format_checks["RAW (ORF)"].setChecked(False)
        app.processEvents()
        assert win._visible_names() == ["/DCIM/100OLYMP/P1010042.JPG"]
        # "Download all" respects the filter (would fetch only the JPEG).
        win.format_checks["JPEG"].setChecked(False)
        app.processEvents()
        assert win._visible_names() == []          # both formats hidden
        # Restore both for the real download below.
        win.format_checks["RAW (ORF)"].setChecked(True)
        win.format_checks["JPEG"].setChecked(True)
        app.processEvents()
        assert len(win._visible_names()) == 2

        # Download all into a temp dir.
        win.output_dir = str(tmp_path)
        done = {}
        win.worker.download_done.connect(
            lambda d, s, f, b: done.update(downloaded=d, skipped=s,
                                           failed=f, bytes=b))
        win._start_download(all_files=True)
        assert _pump(app, lambda: bool(done)), "download did not finish"
        assert done["downloaded"] == 2 and done["failed"] == 0

        files = [p.name for p in tmp_path.rglob("*") if p.is_file()]
        assert any(f.endswith(".JPG") for f in files)
        assert any(f.endswith(".ORF") for f in files)

        win.close()
    finally:
        httpd.shutdown()
        os.environ.pop("OMSHARE_HOST", None)
