"""Test di connessione del proxy di un servizio.

Apre un tunnel con lo stesso protocollo che userà Firefox (HTTP CONNECT,
TLS verso il proxy, SOCKS5 con credenziali, SOCKS4a), poi:
  1. chiede a un servizio di geolocalizzazione con che IP e paese si esce;
  2. verifica che il sito del servizio risponda attraverso il proxy.
Solo libreria standard, nessuna dipendenza.
"""
from __future__ import annotations

import base64
import json
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass

from .core import Proxy

GEO_HOST, GEO_PATH = "ipinfo.io", "/json"
TIMEOUT = 12
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) stream-launcher"

SOCKS5_ERRORS = {
    1: "errore generico del server SOCKS",
    2: "connessione non permessa dalle regole del proxy",
    3: "rete irraggiungibile",
    4: "host irraggiungibile",
    5: "connessione rifiutata",
    6: "TTL scaduto",
    7: "comando non supportato",
    8: "tipo di indirizzo non supportato",
}


class ProxyTestError(Exception):
    pass


@dataclass
class ProxyTestResult:
    ip: str = ""
    country: str = ""
    city: str = ""
    org: str = ""
    latency_ms: int = 0
    service_host: str = ""
    service_status: int = 0
    service_error: str = ""

    def summary(self) -> str:
        where = ", ".join(p for p in (self.city, self.country) if p) or "posizione sconosciuta"
        text = f"Uscita da {self.ip} ({where}) in {self.latency_ms} ms"
        if self.org:
            text += f" — {self.org.rstrip('.')}"
        if self.service_host:
            if self.service_error:
                text += f". {self.service_host}: {self.service_error}"
            else:
                text += f". {self.service_host} risponde (HTTP {self.service_status})"
        return text


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ProxyTestError("il proxy ha chiuso la connessione")
        data += chunk
    return data


def _socks5(sock: socket.socket, proxy: Proxy, host: str, port: int) -> None:
    methods = b"\x00\x02" if proxy.username else b"\x00"
    sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
    ver, method = _recv_exact(sock, 2)
    if ver != 5:
        raise ProxyTestError("non è un proxy SOCKS5")
    if method == 0xFF:
        raise ProxyTestError("il proxy richiede credenziali" if not proxy.username else "metodo di autenticazione rifiutato")
    if method == 2:
        user, pwd = proxy.username.encode(), proxy.password.encode()
        if len(user) > 255 or len(pwd) > 255:
            raise ProxyTestError("utente o password troppo lunghi per SOCKS5")
        sock.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(pwd)]) + pwd)
        if _recv_exact(sock, 2)[1] != 0:
            raise ProxyTestError("utente o password errati")
    target = host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(target)]) + target + port.to_bytes(2, "big"))
    ver, rep, _rsv, atyp = _recv_exact(sock, 4)
    if rep != 0:
        raise ProxyTestError(SOCKS5_ERRORS.get(rep, f"errore SOCKS5 {rep}"))
    addr_len = {1: 4, 4: 16}.get(atyp) or _recv_exact(sock, 1)[0]
    _recv_exact(sock, addr_len + 2)


def _socks4(sock: socket.socket, proxy: Proxy, host: str, port: int) -> None:
    # SOCKS4a: l'host lo risolve il proxy, come fa Firefox con il DNS remoto.
    sock.sendall(
        b"\x04\x01" + port.to_bytes(2, "big") + b"\x00\x00\x00\x01"
        + proxy.username.encode() + b"\x00" + host.encode() + b"\x00"
    )
    reply = _recv_exact(sock, 8)
    if reply[1] != 0x5A:
        raise ProxyTestError(f"richiesta SOCKS4 rifiutata (codice {reply[1]:#x})")


def _http_connect(sock: socket.socket, proxy: Proxy, host: str, port: int) -> None:
    lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}", f"User-Agent: {USER_AGENT}"]
    if proxy.username:
        token = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            raise ProxyTestError("il proxy ha chiuso la connessione")
        head += chunk
        if len(head) > 65536:
            raise ProxyTestError("risposta del proxy non valida")
    status_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = status_line.split(" ", 2)
    code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if code == 407:
        raise ProxyTestError("il proxy richiede credenziali valide (407)" if proxy.username else "il proxy richiede utente e password (407)")
    if code != 200:
        raise ProxyTestError(f"il proxy ha rifiutato il tunnel: {status_line}")


def open_tunnel(proxy: Proxy, host: str, port: int, timeout: float = TIMEOUT) -> socket.socket:
    """Socket già collegato a host:port attraverso il proxy (o diretto se nessun proxy)."""
    if not proxy.enabled:
        return socket.create_connection((host, port), timeout=timeout)
    try:
        sock = socket.create_connection((proxy.host, proxy.port), timeout=timeout)
    except socket.timeout as e:
        raise ProxyTestError(f"{proxy.host}:{proxy.port} non risponde (timeout)") from e
    except OSError as e:
        raise ProxyTestError(f"impossibile raggiungere {proxy.host}:{proxy.port}: {e.strerror or e}") from e
    try:
        if proxy.type == "https":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=proxy.host)
        if proxy.type == "socks5":
            _socks5(sock, proxy, host, port)
        elif proxy.type == "socks4":
            _socks4(sock, proxy, host, port)
        else:
            _http_connect(sock, proxy, host, port)
    except ssl.SSLError as e:
        sock.close()
        raise ProxyTestError(f"TLS verso il proxy fallito: {e.reason or e}") from e
    except socket.timeout as e:
        sock.close()
        raise ProxyTestError("il proxy non ha risposto in tempo") from e
    except (ProxyTestError, OSError):
        sock.close()
        raise
    return sock


def https_get(proxy: Proxy, host: str, path: str, timeout: float = TIMEOUT) -> tuple[int, bytes]:
    """GET HTTPS minimale (HTTP/1.0: niente chunked) attraverso il proxy."""
    raw = open_tunnel(proxy, host, 443, timeout)
    try:
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    except ssl.SSLError as e:
        raw.close()
        raise ProxyTestError(f"TLS verso {host} fallito: {e.reason or e}") from e
    with tls:
        request = (
            f"GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: {USER_AGENT}\r\n"
            "Accept: application/json, text/html;q=0.9\r\nConnection: close\r\n\r\n"
        )
        tls.sendall(request.encode())
        data = b""
        while len(data) < 262144:
            try:
                chunk = tls.recv(16384)
            except ssl.SSLError:
                break  # alcuni server chiudono senza close_notify
            if not chunk:
                break
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    try:
        status = int(head.split(b" ", 2)[1])
    except (IndexError, ValueError) as e:
        raise ProxyTestError(f"risposta non valida da {host}") from e
    return status, body


def test_proxy(proxy: Proxy, service_url: str = "") -> ProxyTestResult:
    """Esegue il test; solleva ProxyTestError con un messaggio leggibile se il proxy non va."""
    result = ProxyTestResult()
    start = time.monotonic()
    try:
        status, body = https_get(proxy, GEO_HOST, GEO_PATH)
    except socket.timeout as e:
        raise ProxyTestError("nessuna risposta attraverso il proxy (timeout)") from e
    except OSError as e:
        raise ProxyTestError(f"connessione fallita: {e.strerror or e}") from e
    result.latency_ms = int((time.monotonic() - start) * 1000)
    if status == 200:
        try:
            info = json.loads(body)
        except ValueError:
            info = {}
        result.ip = info.get("ip", "")
        result.country = info.get("country", "")
        result.city = info.get("city", "")
        result.org = info.get("org", "")
    if not result.ip:
        result.ip = "IP sconosciuto"

    host = urllib.parse.urlparse(service_url).hostname if service_url else ""
    if host:
        result.service_host = host
        try:
            result.service_status, _ = https_get(proxy, host, "/")
        except (ProxyTestError, OSError) as e:
            result.service_error = f"non raggiungibile ({e})"
        else:
            if result.service_status == 403:
                result.service_error = "risponde 403 (blocco geografico, del proxy o anti-bot)"
    return result
