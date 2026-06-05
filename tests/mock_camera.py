"""
A minimal fake Olympus camera HTTP server for offline testing.

Implements just enough of the CGI protocol for olympuswifi's OlympusCamera() to
initialize and for the download path to run: get_commandlist, get_caminfo,
switch_cammode, get_camprop, get_imglist (one subdirectory + two files),
get_thumbnail, and raw file GET.
"""

import http.server
import os
import threading
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(__file__)
COMMANDLIST = os.path.join(HERE, "fixtures", "commandlist.xml")

# FAT-encoded 2026-06-05 12:34:56 (matches olympuswifi's decode).
DATE = ((2026 - 1980) << 9) | (6 << 5) | 5      # 23749
TIME = (12 << 11) | (34 << 5) | (56 // 2)        # 25692


def _fake_jpeg(size: int) -> bytes:
    body = b"\xff\xd8" + b"\x00" * (size - 4) + b"\xff\xd9"
    return body[:size]


FILES = {
    "/DCIM/100OLYMP/P1010042.JPG": _fake_jpeg(120000),
    "/DCIM/100OLYMP/P1010043.ORF": _fake_jpeg(15000000),
}

DESCLIST = (
    '<?xml version="1.0"?>\r\n<desclist>\r\n'
    '<desc><propname>isospeedvalue</propname><attribute>getset</attribute>'
    '<value>Auto</value><enum>Auto 200 400 800 1600</enum></desc>\r\n'
    '<desc><propname>wbvalue</propname><attribute>getset</attribute>'
    '<value>WB_AUTO</value><enum>WB_AUTO MWB_SHADE MWB_CLOUD</enum></desc>\r\n'
    '</desclist>\r\n'
)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _xml(self, s: str, code: int = 200):
        self._send(s.encode("utf-8"), "text/xml", code)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._xml('<?xml version="1.0"?>\r\n<result>ok</result>\r\n')

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path == "/get_commandlist.cgi":
            with open(COMMANDLIST, "rb") as f:
                self._send(f.read(), "text/xml")
        elif path == "/get_caminfo.cgi":
            self._xml('<?xml version="1.0"?>\r\n<caminfo><model>E-M10MarkII'
                      '</model></caminfo>\r\n')
        elif path == "/switch_cammode.cgi":
            self._send(b"", "text/plain")
        elif path == "/get_camprop.cgi":
            if qs.get("com", [""])[0] == "desc":
                self._xml(DESCLIST)
            else:
                self._xml('<?xml version="1.0"?>\r\n<get><value>Auto</value>'
                          '</get>\r\n')
        elif path == "/get_imglist.cgi":
            d = qs.get("DIR", ["/DCIM"])[0]
            if d == "/DCIM":
                body = f"VER_100\r\n/DCIM,100OLYMP,0,16,{DATE},{TIME}\r\n"
            elif d == "/DCIM/100OLYMP":
                lines = ["VER_100"]
                for full, data in FILES.items():
                    dirp, name = full.rsplit("/", 1)
                    lines.append(f"{dirp},{name},{len(data)},32,{DATE},{TIME}")
                body = "\r\n".join(lines) + "\r\n"
            else:
                self._send(b"not found", "text/plain", 404)
                return
            self._send(body.encode("utf-8"), "text/plain")
        elif path == "/get_thumbnail.cgi":
            self._send(_fake_jpeg(8000), "image/jpeg")
        elif path in FILES:
            self._send(FILES[path], "image/jpeg")
        else:
            self._send(b"not found", "text/plain", 404)


def serve(port: int = 0):
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


if __name__ == "__main__":
    import time
    s = serve(8910)
    print("mock camera on", s.server_address, flush=True)
    while True:
        time.sleep(1)
