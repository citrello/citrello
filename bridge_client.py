"""
Citrello account and bridge connection.

Signing in (email + one-time code) links this computer to a Citrello account. While the app runs, it
keeps a WebSocket open to the Citrello bridge (mcp.citrello.app), which passes requests from ChatGPT or
Claude on the web to this computer's library and returns the answers. The library itself never leaves
the computer.

Standard library only (a small WebSocket client is included).
"""
import base64
import hashlib
import json
import os
import platform
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import citrello_core as core

BRIDGE = (os.environ.get("CITRELLO_BRIDGE") or "https://mcp.citrello.app").rstrip("/")
# Per computer (not in the library folder, which may be shared through OneDrive).
ACCOUNT_FILE = os.environ.get("CITRELLO_ACCOUNT_FILE") or os.path.join(os.path.expanduser("~"), ".citrello_account.json")


class BridgeError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


# ---------------------------------------------------------------------------
# Account file
# ---------------------------------------------------------------------------

def load_account():
    try:
        with open(ACCOUNT_FILE, encoding="utf-8") as f:
            a = json.load(f)
        return a if a.get("token") else None
    except (OSError, ValueError):
        return None


def _save_account(a):
    tmp = ACCOUNT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, indent=1)
    os.replace(tmp, ACCOUNT_FILE)
    try:
        os.chmod(ACCOUNT_FILE, 0o600)
    except OSError:
        pass


def _clear_account():
    try:
        os.remove(ACCOUNT_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

def api(method, path, body=None, token=None, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BRIDGE + path, data=data, method=method)
    req.add_header("User-Agent", "Citrello/" + core.VERSION)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        raise BridgeError(err.get("error_description") or f"Citrello server error ({e.code})", e.code, err.get("error"))
    except (urllib.error.URLError, OSError) as e:
        raise BridgeError("Can't reach the Citrello server. Check your internet connection.") from e


def start_sign_in(email):
    """Emails a 6-digit code."""
    return api("POST", "/api/auth/start", {"email": email})


def device_name():
    return (platform.node() or "Computer")[:80]


def finish_sign_in(email, code, newsletter=False):
    res = api("POST", "/api/auth/verify", {
        "email": email, "code": code, "newsletter": bool(newsletter), "device_name": device_name(),
        "platform": f"{platform.system()} {platform.release()}".strip(), "app_version": core.VERSION})
    _save_account({"token": res["token"], "device_id": res["device_id"], "email": res["user"]["email"],
                   "user_id": res["user"]["id"], "bridge": BRIDGE})
    CONNECTION.wake()
    return res["user"]


def account_info():
    """Account details from the server (devices, newsletter, connector address)."""
    a = load_account()
    if not a:
        return None
    try:
        return api("GET", "/api/me", token=a["token"])
    except BridgeError as e:
        if e.status == 401:
            _signed_out_remotely()
        raise


def set_newsletter(on):
    a = load_account()
    if not a:
        raise BridgeError("Not signed in")
    return api("POST", "/api/me", {"newsletter": bool(on)}, token=a["token"])


def remove_device(device_id):
    a = load_account()
    if not a:
        raise BridgeError("Not signed in")
    return api("POST", "/api/signout", {"device_id": device_id}, token=a["token"])


def sign_out():
    a = load_account()
    if a:
        try:
            api("POST", "/api/signout", {}, token=a["token"], timeout=10)
        except BridgeError:
            pass  # signing out locally still works offline
    _clear_account()
    CONNECTION.disconnect()


def delete_account():
    a = load_account()
    if not a:
        raise BridgeError("Not signed in")
    api("POST", "/api/account/delete", {}, token=a["token"])
    _clear_account()
    CONNECTION.disconnect()


def _signed_out_remotely():
    _clear_account()
    CONNECTION.disconnect()


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455), with HTTP proxy support
# ---------------------------------------------------------------------------

class WebSocketError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _proxy_for(url):
    if urllib.request.proxy_bypass(url.hostname or ""):
        return None
    proxies = urllib.request.getproxies()
    p = proxies.get("https" if url.scheme in ("wss", "https") else "http")
    return urllib.parse.urlsplit(p if "://" in p else "http://" + p) if p else None


def _read_headers(sock, buf=b""):
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("Connection closed during handshake")
        buf += chunk
        if len(buf) > 65536:
            raise WebSocketError("Handshake response too large")
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise WebSocketError("Bad handshake response")
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    return status, headers, rest


class WebSocket:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, url, headers=None, timeout=20):
        u = urllib.parse.urlsplit(url)
        secure = u.scheme in ("wss", "https")
        host, port = u.hostname, u.port or (443 if secure else 80)
        proxy = _proxy_for(u)
        if proxy:
            sock = socket.create_connection((proxy.hostname, proxy.port or 8080), timeout)
            auth = ""
            if proxy.username:
                cred = base64.b64encode(f"{urllib.parse.unquote(proxy.username)}:"
                                        f"{urllib.parse.unquote(proxy.password or '')}".encode()).decode()
                auth = f"Proxy-Authorization: Basic {cred}\r\n"
            sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n{auth}\r\n".encode())
            status, _, _ = _read_headers(sock)
            if status != 200:
                sock.close()
                raise WebSocketError(f"Proxy refused the connection ({status})")
        else:
            sock = socket.create_connection((host, port), timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        host_header = host if u.port is None else f"{host}:{port}"
        extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{extra}\r\n").encode())
        status, resp_headers, rest = _read_headers(sock)
        if status != 101:
            sock.close()
            raise WebSocketError(f"Server refused the connection ({status})", status)
        expect = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
        if resp_headers.get("sec-websocket-accept") != expect:
            sock.close()
            raise WebSocketError("Bad handshake")
        self.sock, self.buf = sock, rest
        self.send_lock = threading.Lock()
        self.closed = False

    def settimeout(self, t):
        self.sock.settimeout(t)

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("Connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if n < 4096 else _mask(payload, mask)
        with self.send_lock:
            self.sock.sendall(bytes(header) + mask + masked)

    def send(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self):
        """Next text message, or None when the server closes the connection."""
        parts, kind = [], None
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b2 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = _mask(payload, mask)
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
            elif opcode == 0xA:  # pong
                pass
            elif opcode == 0x8:  # close
                try:
                    self._send_frame(0x8, payload[:2])
                except OSError:
                    pass
                self.closed = True
                return None
            else:
                if opcode in (0x1, 0x2):
                    kind = opcode
                parts.append(payload)
                if fin:
                    data = b"".join(parts)
                    return data.decode("utf-8", "replace") if kind == 0x1 else data

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self._send_frame(0x8, struct.pack(">H", 1000))
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


def _mask(payload, mask):
    n = len(payload)
    m = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(payload, "big") ^ m).to_bytes(n, "big") if n else b""


# ---------------------------------------------------------------------------
# Bridge connection
# ---------------------------------------------------------------------------

def _mcp():
    """The connector's request handler (the same tools Claude Desktop gets)."""
    stdout = sys.stdout
    try:
        import mcp_server  # it redirects stdout while importing
    finally:
        sys.stdout = stdout
    return mcp_server


class Connection:
    PING_EVERY = 30
    READ_TIMEOUT = 75

    def __init__(self):
        self.state = "signed_out"  # signed_out / connecting / online / offline
        self.error = ""
        self.since = None
        self._ws = None
        self._thread = None
        self._wake = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="citrello-bridge", daemon=True)
            self._thread.start()

    def wake(self):
        self.start()
        self._wake.set()

    def disconnect(self):
        ws = self._ws
        if ws:
            ws.close()
        self._wake.set()

    def status(self):
        a = load_account()
        return {"signed_in": bool(a), "email": a and a.get("email"), "state": self.state if a else "signed_out",
                "error": self.error, "online_since": self.since, "bridge": BRIDGE}

    def _run(self):
        delay = 2
        while True:
            a = load_account()
            if not a:
                self.state, self.error = "signed_out", ""
                self._wake.wait()
                self._wake.clear()
                continue
            self.state = "connecting"
            try:
                self._session(a)
                delay = 2
            except WebSocketError as e:
                if e.status == 401:
                    _clear_account()
                    self.error = "This computer was signed out. Sign in again to use the connectors."
                    continue
                self.error = str(e)
            except (OSError, ConnectionError, ValueError) as e:
                self.error = f"Connection lost ({e.__class__.__name__})"
            self.state, self.since, self._ws = "offline", None, None
            self._wake.wait(delay)
            self._wake.clear()
            delay = min(delay * 2, 60)

    def _session(self, a):
        url = BRIDGE.replace("https://", "wss://").replace("http://", "ws://") + "/device/connect"
        ws = WebSocket(url, {"Authorization": "Bearer " + a["token"], "User-Agent": "Citrello/" + core.VERSION})
        self._ws = ws
        m = _mcp()
        ws.send(json.dumps({"type": "hello", "version": core.VERSION, "instructions": m.INSTRUCTIONS,
                            "serverInfo": {"name": "citrello", "version": core.VERSION},
                            "tools": m.handle({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})["result"]["tools"]}))
        self.state, self.error, self.since = "online", "", time.time()
        ws.settimeout(self.READ_TIMEOUT)
        stop = threading.Event()

        def pinger():
            while not stop.wait(self.PING_EVERY):
                try:
                    ws.send("ping")
                except OSError:
                    return

        threading.Thread(target=pinger, daemon=True).start()
        try:
            while True:
                data = ws.recv()
                if data is None:
                    return
                if data == "pong" or not isinstance(data, str):
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if msg.get("type") == "rpc":
                    threading.Thread(target=self._answer, args=(ws, msg), daemon=True).start()
        finally:
            stop.set()
            ws.close()

    @staticmethod
    def _answer(ws, msg):
        m = _mcp()
        try:
            with m.remote_call():
                reply = m.handle(msg.get("msg") or {})
        except Exception as e:  # never leave the remote side waiting
            reply = {"jsonrpc": "2.0", "id": (msg.get("msg") or {}).get("id"),
                     "error": {"code": -32603, "message": f"Citrello error: {e}"}}
        try:
            ws.send(json.dumps({"type": "reply", "id": msg.get("id"), "msg": reply}, ensure_ascii=False))
        except OSError:
            pass


CONNECTION = Connection()
