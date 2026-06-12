"""Tests for servepass: filename sanitization, token auth, upload safety.

Plain test functions, no pytest-specific features, so the file runs under
pytest in CI and directly via `python tests/test_servepass.py` anywhere.
"""

import os
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import HTTPServer

from servepass.__main__ import (
    generate_token,
    make_token_handler,
    parse_multipart,
    sanitize_filename,
)

TOKEN = "tok-for-tests"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def running_server():
    """Spin up a real TokenHandler server on a random port, serving a tmp dir."""
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "hello.txt"), "w") as f:
            f.write("hi")
        handler = make_token_handler(token=TOKEN, directory=root)
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}", root
        finally:
            httpd.shutdown()


def get_status(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def multipart_body(filename, payload, boundary="testboundary"):
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return content_type, body


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

def test_sanitize_strips_path_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("/etc/shadow") == "shadow"


def test_sanitize_strips_windows_separators():
    assert sanitize_filename("..\\..\\evil.txt") == "evil.txt"
    assert sanitize_filename("C:\\Users\\victim\\evil.exe") == "evil.exe"


def test_sanitize_strips_null_bytes():
    assert sanitize_filename("safe\x00.txt") == "safe.txt"


def test_sanitize_empty_and_dot_names_fall_back():
    assert sanitize_filename("") == "upload"
    assert sanitize_filename(".") == "upload"
    assert sanitize_filename("..") == "upload"
    assert sanitize_filename("/") == "upload"


def test_sanitize_keeps_normal_names():
    assert sanitize_filename("report-final (2).pdf") == "report-final (2).pdf"


# ---------------------------------------------------------------------------
# generate_token
# ---------------------------------------------------------------------------

def test_token_is_eight_hex_chars():
    token = generate_token()
    assert len(token) == 8
    int(token, 16)  # raises if not hex


def test_tokens_are_not_repeated():
    assert len({generate_token() for _ in range(50)}) == 50


# ---------------------------------------------------------------------------
# parse_multipart
# ---------------------------------------------------------------------------

def test_parse_multipart_roundtrip():
    content_type, body = multipart_body("notes.txt", b"payload-bytes")
    filename, data = parse_multipart(content_type, body)
    assert filename == "notes.txt"
    assert data == b"payload-bytes"


def test_parse_multipart_sanitizes_filename():
    content_type, body = multipart_body("../../sneaky.txt", b"x")
    filename, _ = parse_multipart(content_type, body)
    assert filename == "sneaky.txt"


# ---------------------------------------------------------------------------
# Token auth over HTTP
# ---------------------------------------------------------------------------

def test_valid_token_is_accepted():
    with running_server() as (base, _root):
        assert get_status(f"{base}/?token={TOKEN}") == 200


def test_missing_token_is_denied():
    with running_server() as (base, _root):
        assert get_status(f"{base}/") == 403


def test_wrong_token_is_denied():
    with running_server() as (base, _root):
        assert get_status(f"{base}/?token=wrong") == 403
        assert get_status(f"{base}/?token={TOKEN[:-1]}") == 403


# ---------------------------------------------------------------------------
# Upload safety
# ---------------------------------------------------------------------------

def test_upload_with_traversal_name_stays_inside_root():
    with running_server() as (base, root):
        content_type, body = multipart_body("../../escape.txt", b"contained")
        req = urllib.request.Request(
            f"{base}/?token={TOKEN}",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200  # after redirect back to listing

        inside = os.path.join(root, "escape.txt")
        outside = os.path.normpath(os.path.join(root, "..", "escape.txt"))
        assert os.path.exists(inside)
        assert open(inside, "rb").read() == b"contained"
        assert not os.path.exists(outside)


# ---------------------------------------------------------------------------
# Plain runner (no pytest required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    failures = 0
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
    raise SystemExit(failures)