"""
omshare GUI — a cross-platform (Linux / macOS / Windows) thumbnail browser for
downloading photos/videos from an Olympus camera over WiFi.

All camera and WiFi I/O happens in a single background worker thread (the camera
serves one HTTP request at a time), so the Qt UI stays responsive. The worker
reuses the same protocol/download code as the CLI:

    omshare.wifi          — join/leave the camera's WiFi access point
    olympuswifi.camera    — the HTTP/CGI protocol
    omshare.download      — listing, resume logic, chunked streaming

Run with:  omshare-gui      (or:  python -m omshare.gui)
"""

import os
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import (QObject, QSize, Qt, QThread, Signal, Slot)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from . import wifi
from . import download as dl

CAMERA_HOST = wifi.CAMERA_HOST
THUMB_SIZE = QSize(160, 120)

# File-format categories for the GUI filter. Olympus cameras typically produce
# JPEG and/or ORF (RAW) — both at once in RAW+JPEG mode — plus movies.
FORMAT_CATEGORIES = {
    "JPEG": (".jpg", ".jpeg"),
    "RAW (ORF)": (".orf",),
    "Video": (".mov", ".mp4", ".avi"),
}


def _category_of(file_name: str) -> Optional[str]:
    low = file_name.lower()
    for cat, exts in FORMAT_CATEGORIES.items():
        if low.endswith(exts):
            return cat
    return None


# --------------------------------------------------------------------------- #
#  Background worker (lives in its own QThread)
# --------------------------------------------------------------------------- #

class CameraWorker(QObject):
    """Performs all blocking camera/WiFi work and reports back via signals."""

    status = Signal(str)                         # human-readable status line
    connected = Signal(str)                      # camera model
    connect_failed = Signal(str)                 # error message
    listed = Signal(int, int)                    # count, total_bytes
    thumb = Signal(str, str, int, bytes)         # file_name, date_time, size, jpeg
    list_done = Signal()
    progress = Signal(str, int, int, int, int)   # name, got, total, index, count
    file_done = Signal(str, bool, str)           # name, ok, message
    download_done = Signal(int, int, int, int)   # downloaded, skipped, failed, bytes
    disconnected = Signal()

    # Request signals (emitted from the UI thread, executed here):
    req_connect = Signal(str, str, str)          # ssid, password, iface
    req_list = Signal()
    req_download = Signal(list, str, str)        # file_names, outdir, organize
    req_disconnect = Signal(str)                 # ssid (or "")

    def __init__(self):
        super().__init__()
        self.camera = None
        self.files: Dict[str, object] = {}       # file_name -> FileDescr
        self._joined_ssid: Optional[str] = None
        self.req_connect.connect(self._connect)
        self.req_list.connect(self._list)
        self.req_download.connect(self._download)
        self.req_disconnect.connect(self._disconnect)

    # -- helpers ------------------------------------------------------------ #
    def _apply_host(self, host: str):
        from olympuswifi.camera import OlympusCamera
        OlympusCamera.URL_PREFIX = f"http://{host}/"
        OlympusCamera.HEADERS = {"Host": host, "User-Agent": "OI.Share v2"}

    # -- slots -------------------------------------------------------------- #
    @Slot(str, str, str)
    def _connect(self, ssid: str, password: str, iface: str):
        try:
            env_host = os.environ.get("OMSHARE_HOST")
            if env_host:
                self.status.emit(f"Connecting to {env_host} …")
                self._apply_host(env_host)
            elif ssid:
                self.status.emit(f"Joining camera WiFi '{ssid}' …")
                self._joined_ssid = wifi.connect(ssid, password,
                                                 iface=iface or None)
                self._apply_host(CAMERA_HOST)
            else:
                self.status.emit("Connecting to camera at "
                                 f"{CAMERA_HOST} (assuming already joined) …")
                self._apply_host(CAMERA_HOST)

            host = (env_host or CAMERA_HOST).split(":")[0]
            port = int((env_host or "").split(":")[1]) if env_host and ":" in env_host else 80
            if not wifi.is_camera_reachable(host, port):
                raise wifi.WifiError(
                    f"Camera not reachable. If you're not on the camera's WiFi, "
                    "enter its SSID/password above. Make sure WiFi is enabled on "
                    "the camera (it sleeps after ~1 min idle).")

            from olympuswifi.camera import OlympusCamera
            self.camera = OlympusCamera()
            self.connected.emit(self.camera.get_camera_model())
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            self.connect_failed.emit(str(e))

    @Slot()
    def _list(self):
        if self.camera is None:
            return
        try:
            self.status.emit("Listing photos on the card …")
            files = self.camera.list_images()
            files.sort(key=lambda f: f.date_time)
            self.files = {f.file_name: f for f in files}
            self.listed.emit(len(files), sum(f.file_size for f in files))
            for f in files:
                try:
                    jpeg = self.camera.download_thumbnail(f.file_name)
                except Exception:  # noqa: BLE001 - a bad thumbnail shouldn't abort
                    jpeg = b""
                self.thumb.emit(f.file_name, f.date_time, f.file_size, jpeg)
            self.list_done.emit()
            self.status.emit(f"{len(files)} item(s) on the card.")
        except Exception as e:  # noqa: BLE001
            self.status.emit(f"Error listing photos: {e}")

    @Slot(list, str, str)
    def _download(self, file_names: List[str], outdir: str, organize: str):
        if self.camera is None:
            return
        targets = [self.files[n] for n in file_names if n in self.files]
        count = len(targets)
        stats = {"downloaded": 0, "skipped": 0, "failed": 0, "bytes": 0}
        for i, fd in enumerate(targets, 1):
            local = dl._local_path(fd, outdir, organize)
            if dl._already_have(local, fd):
                stats["skipped"] += 1
                self.file_done.emit(fd.file_name, True, "already downloaded")
                continue
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            tmp = local + ".part"
            try:
                got = dl._stream_to_file(
                    self.camera, fd, tmp,
                    lambda g, fn=fd.file_name, tot=fd.file_size, idx=i:
                        self.progress.emit(fn, g, tot, idx, count))
                if got != fd.file_size:
                    raise IOError(f"size mismatch (got {got}, "
                                  f"expected {fd.file_size})")
                os.replace(tmp, local)
                import datetime
                dt = datetime.datetime.strptime(fd.date_time, "%Y-%m-%dT%H:%M:%S")
                os.utime(local, (dt.timestamp(), dt.timestamp()))
                stats["downloaded"] += 1
                stats["bytes"] += fd.file_size
                self.file_done.emit(fd.file_name, True, "downloaded")
            except Exception as e:  # noqa: BLE001
                stats["failed"] += 1
                self.file_done.emit(fd.file_name, False, str(e))
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self.download_done.emit(stats["downloaded"], stats["skipped"],
                                stats["failed"], stats["bytes"])

    @Slot(str)
    def _disconnect(self, ssid: str):
        try:
            wifi.disconnect(ssid or self._joined_ssid or None)
        except Exception:  # noqa: BLE001 - best effort
            pass
        self.camera = None
        self._joined_ssid = None
        self.disconnected.emit()


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("omshare — Olympus photo download")
        self.resize(820, 600)
        self._connected = False
        self.output_dir = os.path.join(os.path.expanduser("~"), "Pictures",
                                       "Olympus")

        # --- worker thread ---
        self.thread = QThread(self)
        self.worker = CameraWorker()
        self.worker.moveToThread(self.thread)
        self.thread.start()
        self._wire_worker()

        self._build_ui()
        self._set_connected(False)

    # -- UI construction ---------------------------------------------------- #
    def _build_ui(self):
        root = QVBoxLayout(self)

        # Connection row
        conn = QHBoxLayout()
        self.ssid_edit = QLineEdit(); self.ssid_edit.setPlaceholderText("Camera WiFi SSID")
        self.pw_edit = QLineEdit(); self.pw_edit.setPlaceholderText("Password")
        self.pw_edit.setEchoMode(QLineEdit.Password)
        # Enter in either field triggers Connect.
        self.ssid_edit.returnPressed.connect(self._toggle_connect)
        self.pw_edit.returnPressed.connect(self._toggle_connect)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.status_dot = QLabel("●"); self.status_dot.setStyleSheet("color:#c0392b;")
        self.status_text = QLabel("disconnected")
        conn.addWidget(QLabel("Camera:"))
        conn.addWidget(self.ssid_edit, 2)
        conn.addWidget(self.pw_edit, 1)
        conn.addWidget(self.connect_btn)
        conn.addWidget(self.status_dot)
        conn.addWidget(self.status_text, 1)
        root.addLayout(conn)
        if os.environ.get("OMSHARE_HOST"):
            self.ssid_edit.setPlaceholderText(
                f"(using OMSHARE_HOST={os.environ['OMSHARE_HOST']})")
            self.ssid_edit.setEnabled(False)
            self.pw_edit.setEnabled(False)

        # Destination row
        dest = QHBoxLayout()
        self.dest_label = QLabel(self._dest_text())
        browse = QPushButton("Choose folder…")
        browse.clicked.connect(self._choose_folder)
        dest.addWidget(QLabel("Save to:"))
        dest.addWidget(self.dest_label, 1)
        dest.addWidget(browse)
        root.addLayout(dest)

        # Format filter row (JPEG / RAW / Video)
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Show:"))
        self.format_checks: Dict[str, QCheckBox] = {}
        for cat in FORMAT_CATEGORIES:
            cb = QCheckBox(cat)
            cb.setChecked(True)
            cb.toggled.connect(self._apply_filter)
            self.format_checks[cat] = cb
            filt.addWidget(cb)
        filt.addStretch(1)
        root.addLayout(filt)

        # Thumbnail grid
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(THUMB_SIZE)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setSelectionMode(QListWidget.ExtendedSelection)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setSpacing(8)
        self.grid.setUniformItemSizes(True)
        self.grid.itemSelectionChanged.connect(self._update_counts)
        root.addWidget(self.grid, 1)

        # Bottom row: counts + actions + progress
        bottom = QHBoxLayout()
        self.count_label = QLabel("Not connected.")
        self.dl_selected_btn = QPushButton("Download selected")
        self.dl_all_btn = QPushButton("Download all")
        self.dl_selected_btn.clicked.connect(lambda: self._start_download(False))
        self.dl_all_btn.clicked.connect(lambda: self._start_download(True))
        bottom.addWidget(self.count_label, 1)
        bottom.addWidget(self.dl_selected_btn)
        bottom.addWidget(self.dl_all_btn)
        root.addLayout(bottom)

        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.progress.setFormat("%p%  —  idle")
        root.addWidget(self.progress)

    def _dest_text(self) -> str:
        return self.output_dir.replace(os.path.expanduser("~"), "~")

    # -- worker wiring ------------------------------------------------------ #
    def _wire_worker(self):
        w = self.worker
        w.status.connect(self._on_status)
        w.connected.connect(self._on_connected)
        w.connect_failed.connect(self._on_connect_failed)
        w.listed.connect(self._on_listed)
        w.thumb.connect(self._on_thumb)
        w.list_done.connect(self._on_list_done)
        w.progress.connect(self._on_progress)
        w.file_done.connect(self._on_file_done)
        w.download_done.connect(self._on_download_done)
        w.disconnected.connect(self._on_disconnected)

    # -- UI actions --------------------------------------------------------- #
    def _toggle_connect(self):
        if self._connected:
            self.worker.req_disconnect.emit("")
            self.connect_btn.setEnabled(False)
            return

        ssid = self.ssid_edit.text().strip()
        password = self.pw_edit.text()

        # Require SSID + password before trying to join the camera's WiFi.
        # (Skipped only when OMSHARE_HOST is set, e.g. for testing against a
        # camera you're already connected to, or the mock server.)
        if not os.environ.get("OMSHARE_HOST"):
            if not ssid:
                QMessageBox.information(
                    self, "Camera SSID needed",
                    "Enter the camera's WiFi network name (SSID). It's shown on "
                    "the camera screen under Connection to Smartphone.")
                self.ssid_edit.setFocus()
                return
            if not password:
                QMessageBox.information(
                    self, "Password needed",
                    "Enter the camera's WiFi password. It's shown on the camera "
                    "screen next to the SSID.")
                self.pw_edit.setFocus()
                return

        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting…")
        self.worker.req_connect.emit(ssid, password, "")

    def _format_enabled(self, file_name: str) -> bool:
        """True if this file's format is currently shown (uncategorized always shown)."""
        cat = _category_of(file_name)
        if cat is None:
            return True
        return self.format_checks[cat].isChecked()

    def _apply_filter(self):
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            hidden = not self._format_enabled(item.data(Qt.UserRole))
            item.setHidden(hidden)
            if hidden:
                item.setSelected(False)
        self._update_counts()

    def _visible_names(self) -> List[str]:
        return [self.grid.item(i).data(Qt.UserRole)
                for i in range(self.grid.count())
                if not self.grid.item(i).isHidden()]

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose download folder",
                                             self.output_dir)
        if d:
            self.output_dir = d
            self.dest_label.setText(self._dest_text())

    def _start_download(self, all_files: bool):
        if not self._connected:
            return
        if all_files:
            names = self._visible_names()
        else:
            names = [it.data(Qt.UserRole) for it in self.grid.selectedItems()
                     if not it.isHidden()]
        if not names:
            QMessageBox.information(self, "Nothing selected",
                                    "Select one or more photos, or use "
                                    "“Download all” . (Check the Show filters "
                                    "if the grid looks empty.)")
            return
        self._set_busy(True)
        self.progress.setValue(0)
        self.worker.req_download.emit(names, self.output_dir, "date")

    # -- worker callbacks --------------------------------------------------- #
    @Slot(str)
    def _on_status(self, msg: str):
        self.count_label.setText(msg)

    @Slot(str)
    def _on_connected(self, model: str):
        self._set_connected(True)
        self.status_text.setText(f"connected — {model}")
        self.worker.req_list.emit()

    @Slot(str)
    def _on_connect_failed(self, msg: str):
        self._set_connected(False)
        QMessageBox.critical(self, "Connection failed", msg)

    @Slot(int, int)
    def _on_listed(self, count: int, total: int):
        self.grid.clear()
        self._total_count = count
        self.count_label.setText(
            f"{count} item(s), {dl._human(total)} — loading thumbnails…")

    @Slot(str, str, int, bytes)
    def _on_thumb(self, name: str, date_time: str, size: int, jpeg: bytes):
        item = QListWidgetItem(name.split("/")[-1])
        item.setData(Qt.UserRole, name)
        item.setToolTip(f"{name}\n{date_time}  ·  {dl._human(size)}")
        if jpeg:
            pm = QPixmap()
            if pm.loadFromData(jpeg):
                item.setIcon(QIcon(pm))
        item.setHidden(not self._format_enabled(name))
        self.grid.addItem(item)
        self._update_counts()

    @Slot()
    def _on_list_done(self):
        self._update_counts()

    @Slot(str, int, int, int, int)
    def _on_progress(self, name: str, got: int, total: int, index: int, count: int):
        pct = int(100 * got / total) if total else 0
        self.progress.setValue(pct)
        self.progress.setFormat(
            f"%p%  —  [{index}/{count}] {name.split('/')[-1]} "
            f"({dl._human(got)}/{dl._human(total)})")

    @Slot(str, bool, str)
    def _on_file_done(self, name: str, ok: bool, msg: str):
        pass  # individual results summarized in download_done

    @Slot(int, int, int, int)
    def _on_download_done(self, downloaded: int, skipped: int, failed: int,
                          nbytes: int):
        self._set_busy(False)
        self.progress.setValue(100 if downloaded or skipped else 0)
        self.progress.setFormat("%p%  —  done")
        self.count_label.setText(
            f"Done: {downloaded} downloaded ({dl._human(nbytes)}), "
            f"{skipped} skipped, {failed} failed.")
        if failed:
            QMessageBox.warning(self, "Some downloads failed",
                                f"{failed} file(s) failed (the camera may have "
                                "dropped to standby). Re-run to resume.")

    @Slot()
    def _on_disconnected(self):
        self._set_connected(False)
        self.grid.clear()
        self.count_label.setText("Disconnected.")

    # -- UI state helpers --------------------------------------------------- #
    def _set_connected(self, connected: bool):
        self._connected = connected
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.status_dot.setStyleSheet(
            "color:#27ae60;" if connected else "color:#c0392b;")
        if not connected:
            self.status_text.setText("disconnected")
        self.dl_selected_btn.setEnabled(connected)
        self.dl_all_btn.setEnabled(connected)

    def _set_busy(self, busy: bool):
        self.connect_btn.setEnabled(not busy)
        self.dl_selected_btn.setEnabled(not busy and self._connected)
        self.dl_all_btn.setEnabled(not busy and self._connected)

    def _update_counts(self):
        if not self._connected:
            return
        total = self.grid.count()
        visible = len(self._visible_names())
        sel = len([it for it in self.grid.selectedItems() if not it.isHidden()])
        shown = f"{visible} shown" if visible != total else f"{total} item(s)"
        self.count_label.setText(
            f"{shown} · {sel} selected" if sel
            else f"{shown} · click to select, or “Download all”.")

    # -- shutdown ----------------------------------------------------------- #
    def closeEvent(self, event):
        try:
            self.worker.req_disconnect.emit("")
        except Exception:  # noqa: BLE001
            pass
        self.thread.quit()
        self.thread.wait(3000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("omshare")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
