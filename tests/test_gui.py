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


def test_gui_requires_ssid_and_password(monkeypatch):
    """Connecting by WiFi must prompt for both SSID and password."""
    monkeypatch.delenv("OMSHARE_HOST", raising=False)  # force the WiFi path
    import omshare.gui as gui

    app = QApplication.instance() or QApplication([])
    prompts = []
    # Stub the modal dialog so the test doesn't block, and record the title.
    monkeypatch.setattr(gui.QMessageBox, "information",
                        lambda *a, **k: prompts.append(a[1] if len(a) > 1 else ""))
    win = gui.MainWindow()
    try:
        # Nothing entered -> asks for the SSID, does not attempt to connect.
        win.ssid_edit.setText("")
        win.pw_edit.setText("")
        win._toggle_connect()
        assert prompts and "SSID" in prompts[-1]
        assert win.connect_btn.text() == "Connect" and not win._connected

        # SSID but no password -> asks for the password.
        win.ssid_edit.setText("E-M10II-ABC123")
        win.pw_edit.setText("")
        win._toggle_connect()
        assert "Password" in prompts[-1]
        assert win.connect_btn.text() == "Connect" and not win._connected
    finally:
        win.close()


def test_gui_use_current_wifi_skips_ssid(monkeypatch):
    """Ticking 'Use current Wi-Fi connection' connects with no SSID/password."""
    monkeypatch.delenv("OMSHARE_HOST", raising=False)  # force the WiFi path
    import omshare.gui as gui

    app = QApplication.instance() or QApplication([])
    prompts = []
    monkeypatch.setattr(gui.QMessageBox, "information",
                        lambda *a, **k: prompts.append(a[1] if len(a) > 1 else ""))
    win = gui.MainWindow()
    try:
        # Replace the real (network-touching) connect handler with a recorder.
        win.worker.req_connect.disconnect()
        captured = []
        win.worker.req_connect.connect(
            lambda s, p, i: captured.append((s, p, i)))

        # Ticking the box disables the SSID/password fields.
        win.use_current_cb.setChecked(True)
        assert not win.ssid_edit.isEnabled()
        assert not win.pw_edit.isEnabled()

        # Connect with blank fields: no prompt, and connect requested with
        # empty credentials (so the worker uses the already-joined camera).
        win.ssid_edit.setText("")
        win.pw_edit.setText("")
        win._toggle_connect()
        assert _pump(app, lambda: bool(captured)), "connect was not requested"
        assert not prompts, "should not prompt when using current Wi-Fi"
        assert captured == [("", "", "")]
    finally:
        win.close()


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
