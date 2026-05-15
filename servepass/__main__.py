"""
servepass -- serve files on the LAN with token auth.

Nothing fancy. No dependencies. Just stdlib and a link.
"""

import argparse
import os
import secrets
import signal
import socket
import sys
from http.server import SimpleHTTPRequestHandler, HTTPServer
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
# Request handlers
# ---------------------------------------------------------------------------

def make_token_handler(token, directory):
    """Handler that checks for ?token=<token> on every request."""

    class TokenHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

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

        def log_message(self, format, *args):
            print(f"  {self.address_string()}  {args[0]}  {args[1]}")

    return TokenHandler


def make_open_handler(directory):
    """Handler with no auth, for --no-auth mode."""

    class OpenHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, format, *args):
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
    token = generate_token() if use_auth else None
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