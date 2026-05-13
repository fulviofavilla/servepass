# servepass

Serve files on your LAN with a token. That's it.

```
pipx install servepass
servepass
```

---

You know `python -m http.server`. It's great for quick file sharing on a local network. The problem is it has zero authentication, so anyone on the same network can grab whatever you're serving.

`servepass` fixes that. Same zero-config philosophy, but with a token-protected URL. Run it, get a link, share it. No accounts, no cloud, no telemetry.

## Install

```bash
pipx install servepass
```

Or with pip:

```bash
pip install servepass
```

Requires Python 3.8+. No external dependencies.

## Usage

```bash
# Serve current directory
servepass

# Serve a specific path
servepass ./downloads

# Custom port
servepass --port 9090

# No authentication
servepass --no-auth
```

Every time you run servepass, a fresh token is generated. You get a URL and an optional QR code in the terminal:

```
  Serving:  /home/user/downloads
  Network:  http://192.168.1.42:8080/?token=a3f9b2c1
  Token:    a3f9b2c1

  Ctrl+C to stop
```

Open the URL in any browser on your network and you're in. Scan the QR code to open it directly on your phone.

## QR code

The QR code is printed automatically if the `qrcode` package is installed:

```bash
pip install qrcode
```

Without it, servepass works normally and just shows the URL.

## Known limitations

servepass uses a token in the URL over plain HTTP. The token is not encrypted in transit. This is fine for a trusted local network. It is not a substitute for HTTPS or VPN on public or untrusted networks.

If you're on public WiFi, use something else.

## Who this is for

* You're on a trusted LAN (home, office, workshop) and want to share files quickly
* You don't want to expose an unauthenticated HTTP server to everyone on the network
* You want something lighter than filebrowser or Samba with zero setup time

## Comparison

| | servepass | python -m http.server | filebrowser |
|---|---|---|---|
| Authentication | Token | No | Yes |
| Install | `pipx install servepass` | Built-in | Binary + config |
| Dependencies | None | None | None |
| LAN IP detection | Yes | No | Yes |
| QR code | Optional | No | No |
| File upload | Planned | No | Yes |

## Roadmap

- [x] Serve with token auth
- [x] Auto-detect LAN IP
- [x] QR code in terminal (optional)
- [ ] `--token` flag to set a fixed token
- [ ] HTTPS with self-signed cert
- [ ] File upload via browser

## License

MIT