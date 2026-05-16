"""
servepass -- serve files on the LAN with token auth.

Nothing fancy. No dependencies. Just stdlib and a link.
"""

import argparse
import email.parser
import io
import os
import secrets
import signal
import socket
import sys
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs


from servepass import __version__

DEFAULT_PORT = 8080


# ---------------------------------------------------------------------------
# QR code -- pure stdlib, no external deps
# ---------------------------------------------------------------------------

def _qr_terminal(url):
    """
    Print a QR code for the given URL using Unicode block characters.
    Requires the 'qrcode' package. If not installed, silently skips.
    This is intentionally optional -- the URL is always printed anyway.
    """
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        print()
        for i in range(0, len(matrix) - 1, 2):
            row = "  "
            for j in range(len(matrix[i])):
                top = matrix[i][j]
                bot = matrix[i + 1][j] if i + 1 < len(matrix) else False
                if top and bot:
                    row += "\u2588"  # full block
                elif top:
                    row += "\u2580"  # upper half
                elif bot:
                    row += "\u2584"  # lower half
                else:
                    row += " "
            print(row)
        print()
    except ImportError:
        # qrcode not installed, no problem -- URL is printed in the banner
        pass


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def get_lan_ip():
    """Best-effort local IP detection. Falls back to 127.0.0.1 if it fails."""
    try:
        # connect to an external address but don't actually send anything
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------

def generate_token():
    """8 hex chars -- short enough to type, long enough to not guess."""
    return secrets.token_hex(4)


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name):
    """
    Strip path components and null bytes from an uploaded filename.
    Only the final filename component is kept -- no directory traversal possible.
    """
    name = name.replace("\x00", "")
    name = Path(name).name
    return name or "upload"


def parse_multipart(content_type, body):
    """
    Parse a multipart/form-data body without using the deprecated cgi module.
    Returns (filename, file_bytes) or raises ValueError on parse failure.
    """
    message = f"Content-Type: {content_type}\r\n\r\n".encode() + body
    msg = email.parser.BytesParser().parsebytes(message)

    if not msg.is_multipart():
        raise ValueError("Not a multipart request")

    for part in msg.get_payload():
        disposition = part.get("Content-Disposition", "")
        if 'name="file"' not in disposition and "name=file" not in disposition:
            continue
        filename = None
        for segment in disposition.split(";"):
            segment = segment.strip()
            if segment.startswith("filename="):
                filename = segment[9:].strip().strip('"')
                break
        if filename is None:
            continue
        return sanitize_filename(filename), part.get_payload(decode=True)

    raise ValueError("No file part found in multipart body")


# ---------------------------------------------------------------------------
# Upload form HTML
# ---------------------------------------------------------------------------

UPLOAD_FORM = """
<style>
  body {{ max-width: 860px; margin: 0 auto; padding: 0 1em; }}
  .servepass-upload {{
    margin: 1em 0 1.5em 0;
    display: flex;
    gap: 0.5em;
    align-items: center;
    flex-wrap: wrap;
  }}
  .servepass-upload input[type=file] {{
    flex: 1;
    min-width: 0;
    font-size: 1em;
  }}
  .servepass-upload button {{
    padding: 0.4em 1.2em;
    font-size: 1em;
    cursor: pointer;
  }}
</style>
<form class="servepass-upload" method="POST" action="{action}" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <button type="submit">Upload</button>
</form>
"""


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

def make_token_handler(token, directory):
    """Handler that checks for ?token=<token> on every request."""

    class TokenHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_POST(self):
            if not self.check_token():
                return
            self._handle_upload()

        def _handle_upload(self):
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._respond(400, b"Expected multipart/form-data")
                return

            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._respond(400, b"Empty request")
                return

            body = self.rfile.read(length)

            try:
                filename, data = parse_multipart(content_type, body)
            except ValueError as e:
                self._respond(400, str(e).encode())
                return

            # resolve destination and verify it stays inside the served directory
            dest_dir = os.path.realpath(directory)
            current_path = os.path.realpath(
                os.path.join(dest_dir, urlparse(self.path).path.lstrip("/"))
            )
            if not current_path.startswith(dest_dir):
                self._respond(403, b"Invalid path")
                return

            dest = os.path.join(current_path, filename)

            try:
                with open(dest, "wb") as f:
                    f.write(data)
            except OSError as e:
                self._respond(500, str(e).encode())
                return

            print(f"  upload  {self.address_string()}  {filename}  ({len(data)} bytes)")

            redirect_path = urlparse(self.path).path
            self.send_response(303)
            self.send_header("Location", f"{redirect_path}?token={token}")
            self.end_headers()

        def _respond(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def list_directory(self, path):
            """Generate directory listing with upload form injected."""
            try:
                entries = os.listdir(path)
            except OSError:
                self.send_error(403, "No permission to list directory")
                return None

            entries.sort(key=lambda a: a.lower())

            action = f"{urlparse(self.path).path}?token={token}"
            form = UPLOAD_FORM.format(action=action)

            display_path = os.path.abspath(path)
            rows = []
            for name in entries:
                fullname = os.path.join(path, name)
                display = name + "/" if os.path.isdir(fullname) else name
                link = name + "/" if os.path.isdir(fullname) else name
                rows.append(f'<li><a href="{link}?token={token}">{display}</a></li>')

            html = f"""<!DOCTYPE HTML>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Directory listing for {display_path}</title>
</head>
<body>
<h1>Directory listing for {display_path}</h1>
{form}
<hr>
<ul>
{''.join(rows)}
</ul>
<hr>
</body>
</html>
"""
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            return io.BytesIO(encoded)

        def do_HEAD(self):
            if not self.check_token():
                return
            super().do_HEAD()

        def do_GET(self):
            if not self.check_token():
                return
            # strip token from path before serving so directory listing works
            self.path = self._strip_token(self.path)
            super().do_GET()

        def check_token(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            supplied = params.get("token", [None])[0]
            if supplied == token:
                return True
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Access denied. Missing or invalid token.")
            return False

        def _strip_token(self, path):
            """Remove ?token=... from path so links in directory listing stay clean."""
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            params.pop("token", None)
            # rebuild query string without token
            new_query = "&".join(f"{k}={v[0]}" for k, v in params.items())
            return parsed.path + (f"?{new_query}" if new_query else "")

        def log_error(self, format, *args):
            # suppress TLS handshake attempts on plain HTTP
            msg = (format % args) if args else format
            if any(x in msg for x in ("Bad request", "Bad HTTP")):
                return
            super().log_error(format, *args)

        def log_message(self, format, *args):
            # suppress the garbled lines that accompany TLS bad requests
            if args and isinstance(args[1], str) and args[1] == "400":
                return
            print(f"  {self.address_string()}  {args[0]}  {args[1]}")

    return TokenHandler


def make_open_handler(directory):
    """Handler with no auth, for --no-auth mode."""

    class OpenHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_POST(self):
            self._handle_upload()

        def _handle_upload(self):
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._respond(400, b"Expected multipart/form-data")
                return

            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._respond(400, b"Empty request")
                return

            body = self.rfile.read(length)

            try:
                filename, data = parse_multipart(content_type, body)
            except ValueError as e:
                self._respond(400, str(e).encode())
                return

            dest_dir = os.path.realpath(directory)
            current_path = os.path.realpath(
                os.path.join(dest_dir, urlparse(self.path).path.lstrip("/"))
            )
            if not current_path.startswith(dest_dir):
                self._respond(403, b"Invalid path")
                return

            dest = os.path.join(current_path, filename)

            try:
                with open(dest, "wb") as f:
                    f.write(data)
            except OSError as e:
                self._respond(500, str(e).encode())
                return

            print(f"  upload  {self.address_string()}  {filename}  ({len(data)} bytes)")

            self.send_response(303)
            self.send_header("Location", urlparse(self.path).path)
            self.end_headers()

        def _respond(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def list_directory(self, path):
            try:
                entries = os.listdir(path)
            except OSError:
                self.send_error(403, "No permission to list directory")
                return None

            entries.sort(key=lambda a: a.lower())

            display_path = os.path.abspath(path)
            form = UPLOAD_FORM.format(action=urlparse(self.path).path)
            rows = []
            for name in entries:
                fullname = os.path.join(path, name)
                display = name + "/" if os.path.isdir(fullname) else name
                link = name + "/" if os.path.isdir(fullname) else name
                rows.append(f'<li><a href="{link}">{display}</a></li>')

            html = f"""<!DOCTYPE HTML>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Directory listing for {display_path}</title>
</head>
<body>
<h1>Directory listing for {display_path}</h1>
{form}
<hr>
<ul>
{''.join(rows)}
</ul>
<hr>
</body>
</html>
"""
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            return io.BytesIO(encoded)

        def log_error(self, format, *args):
            msg = (format % args) if args else format
            if any(x in msg for x in ("Bad request", "Bad HTTP")):
                return
            super().log_error(format, *args)

        def log_message(self, format, *args):
            if args and isinstance(args[1], str) and args[1] == "400":
                return
            print(f"  {self.address_string()}  {args[0]}  {args[1]}")

    return OpenHandler


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

def print_banner(directory, ip, port, token):
    url = f"http://{ip}:{port}"
    if token:
        full_url = f"{url}/?token={token}"
        print()
        print(f"  Serving:  {os.path.abspath(directory)}")
        print(f"  Network:  {full_url}")
        print(f"  Token:    {token}")
        _qr_terminal(full_url)
    else:
        print()
        print(f"  Serving:  {os.path.abspath(directory)}")
        print(f"  Network:  {url}")
        print(f"  Auth:     disabled (--no-auth)")
        print()

    print("  Ctrl+C to stop")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="servepass",
        description="Serve files on the LAN with token auth.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  servepass                  serve current directory
  servepass ./downloads      serve a specific directory
  servepass --port 9090      use a custom port
  servepass --no-auth        serve without authentication
        """,
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default=".",
        metavar="DIR",
        help="directory to serve (default: current directory)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="disable authentication",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        metavar="TOKEN",
        help="use a fixed token instead of a random one",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args()

    directory = args.dir
    if not os.path.isdir(directory):
        print(f"servepass: '{directory}' is not a directory", file=sys.stderr)
        sys.exit(1)

    use_auth = not args.no_auth
    if use_auth:
        token = args.token if args.token else generate_token()
    else:
        token = None
    ip = get_lan_ip()

    if use_auth:
        handler = make_token_handler(token=token, directory=directory)
    else:
        handler = make_open_handler(directory=directory)

    server = HTTPServer(("0.0.0.0", args.port), handler)

    def handle_exit(sig, frame):
        print("\n\n  Stopped.\n")
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    print_banner(directory, ip, args.port, token)
    server.serve_forever()


if __name__ == "__main__":
    main()