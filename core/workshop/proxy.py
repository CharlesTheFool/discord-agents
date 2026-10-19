"""Egress proxy (spec 5.6): the only way out of a workshop sandbox.

Each sandbox's network policy allows exactly one destination: this proxy on
the host. Here every connection must:
  - carry a scope's credential (Proxy-Authorization: Basic), else 407;
  - be a CONNECT to port 443 - nothing else, open-internet scopes included;
  - name a host, not an IP literal, on the scope's allowlist (exact match)
    unless the scope is open-internet;
  - resolve, through OUR resolver, only to public addresses (IPv4-mapped,
    NAT64, 6to4 and Teredo forms of private addresses refused too); the
    tunnel then goes to the exact address that was checked, so a second
    DNS answer can't swap in a private one;
  - open TLS with an SNI equal to the CONNECT host, so a tunnel to an
    allowed host can't ask the same CDN edge for a different name.
The proxy decides; who holds which credential and allowlist is the
workshop's business (`authorize`).
"""
import asyncio
import base64
import ipaddress
import logging
import socket
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, FrozenSet, List, Optional, Tuple

from core.internal_constants import (
    WORKSHOP_PROXY_HANDSHAKE_SECONDS,
    WORKSHOP_PROXY_HEAD_LIMIT,
    WORKSHOP_PROXY_IDLE_SECONDS,
)

logger = logging.getLogger(__name__)

PORT = 443
# Ranges is_global doesn't refuse on its own, or that wrap a private address
_REFUSED_NETS = [ipaddress.ip_network(n) for n in (
    "64:ff9b::/96", "64:ff9b:1::/48",   # NAT64
    "2002::/16",                         # 6to4
    "2001::/32",                         # Teredo
    "::/96",                             # IPv4-compatible (deprecated)
    "0.0.0.0/8",
)]


@dataclass(frozen=True)
class EgressGrant:
    """What one credential may reach right now."""
    label: str                      # the scope key, for logs
    allow: FrozenSet[str]           # exact host names
    open_internet: bool = False     # any public host (still port 443 only)


Authorize = Callable[[str, str], Optional[EgressGrant]]


class Refused(Exception):
    def __init__(self, status: str, reason: str, label: str = "?"):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.label = label


def address_ok(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if any(addr in net for net in _REFUSED_NETS if net.version == addr.version):
        return False
    return addr.is_global and not addr.is_multicast


def parse_connect_target(target: str) -> Tuple[str, int]:
    """'host:port' or '[v6]:port' -> (normalized host, port). Raises ValueError."""
    if not target or any(c in target for c in "@/?#\\ \t"):
        raise ValueError("malformed target")
    if target.startswith("["):
        host, sep, port = target[1:].partition("]:")
        if not sep:
            raise ValueError("malformed IPv6 target")
    else:
        host, sep, port = target.rpartition(":")
        if not sep or ":" in host:
            raise ValueError("target needs host:port")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("bad port")
    host = host.lower().rstrip(".")
    if not host:
        raise ValueError("empty host")
    try:
        ipaddress.ip_address(host)
        return host, int(port)
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise ValueError(f"bad host name ({e})") from None
    return host, int(port)


def parse_sni(hello: bytes) -> Optional[str]:
    """Server name from a ClientHello handshake message (record headers
    stripped), or None when there is none."""
    try:
        if hello[0] != 1:                                   # client_hello
            return None
        p = 4 + 2 + 32                                      # header, version, random
        p += 1 + hello[p]                                   # session id
        p += 2 + int.from_bytes(hello[p:p + 2], "big")      # cipher suites
        p += 1 + hello[p]                                   # compression methods
        end = p + 2 + int.from_bytes(hello[p:p + 2], "big")
        p += 2
        while p + 4 <= end:
            ext_type = int.from_bytes(hello[p:p + 2], "big")
            ext_len = int.from_bytes(hello[p + 2:p + 4], "big")
            body = hello[p + 4:p + 4 + ext_len]
            if ext_type == 0:                               # server_name
                q = 2
                while q + 3 <= len(body):
                    name_type = body[q]
                    name_len = int.from_bytes(body[q + 1:q + 3], "big")
                    if name_type == 0:
                        return body[q + 3:q + 3 + name_len].decode("ascii").lower().rstrip(".")
                    q += 3 + name_len
                return None
            p += 4 + ext_len
    except (IndexError, UnicodeDecodeError):
        return None
    return None


async def read_client_hello(reader: asyncio.StreamReader) -> Tuple[bytes, bytes]:
    """TLS records until one whole ClientHello is in: (raw bytes to forward,
    the handshake message)."""
    raw = hs = b""
    while True:
        header = await reader.readexactly(5)
        if header[0] != 22:                                 # handshake record
            raise ValueError("not a TLS handshake")
        body = await reader.readexactly(int.from_bytes(header[3:5], "big"))
        raw += header + body
        hs += body
        if len(hs) >= 4 and len(hs) >= 4 + int.from_bytes(hs[1:4], "big"):
            return raw, hs
        if len(raw) > 64 * 1024:
            raise ValueError("ClientHello too large")


def _basic_credential(head: bytes) -> Optional[Tuple[str, str]]:
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() != b"proxy-authorization":
            continue
        scheme, _, token = value.strip().partition(b" ")
        if scheme.lower() != b"basic":
            return None
        try:
            user, sep, password = base64.b64decode(token, validate=True).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        return (user, password) if sep else None
    return None


async def _default_resolve(host: str) -> List[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, PORT, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


class EgressProxy:
    def __init__(self, authorize: Authorize, *,
                 resolve: Callable[[str], Awaitable[List[str]]] = _default_resolve,
                 connect=asyncio.open_connection):
        self.authorize = authorize
        self.resolve = resolve
        self.connect = connect
        self.decisions = deque(maxlen=500)   # (label, verdict, detail) for the dashboard
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self, port: int) -> int:
        """Bind 127.0.0.1:port (0 = any free port, tests). Raises OSError
        when the port is taken: sandboxes have this port in their policy."""
        self._server = await asyncio.start_server(
            self.handle, "127.0.0.1", port, limit=WORKSHOP_PROXY_HEAD_LIMIT)
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _decide(self, label: str, verdict: str, detail: str) -> None:
        self.decisions.append((label, verdict, detail))
        logger.info(f"egress {verdict} [{label}] {detail}")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        label = "?"
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),
                                              WORKSHOP_PROXY_HANDSHAKE_SECONDS)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
                return
            grant, host = self._check(head)
            label = grant.label
            ips = await self._resolve_checked(host)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            try:
                raw, hello = await asyncio.wait_for(read_client_hello(reader),
                                                    WORKSHOP_PROXY_HANDSHAKE_SECONDS)
            except (ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                self._decide(label, "closed", f"{host}: no TLS ClientHello")
                return
            sni = parse_sni(hello)
            if sni != host:
                self._decide(label, "refused", f"{host}: SNI {sni!r} is not the CONNECT host")
                return
            upstream = await self._open_upstream(host, ips)
            if upstream is None:
                self._decide(label, "failed", f"{host}: no checked address answered")
                return
            self._decide(label, "allowed", f"{host} via {upstream[2]}")
            up_reader, up_writer, _ = upstream
            up_writer.write(raw)
            await _relay(reader, writer, up_reader, up_writer)
        except Refused as r:
            self._decide(r.label if r.label != "?" else label, "refused", r.reason)
            extra = ('Proxy-Authenticate: Basic realm="workshop"\r\n'
                     if r.status.startswith("407") else "")
            writer.write(f"HTTP/1.1 {r.status}\r\n{extra}Content-Length: 0\r\n"
                         f"Connection: close\r\n\r\n".encode())
            try:
                await writer.drain()
            except ConnectionError:
                pass
        except ConnectionError:
            pass
        finally:
            writer.close()

    def _check(self, head: bytes):
        """Everything decidable from the request head alone, in order."""
        try:
            method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
        except ValueError:
            raise Refused("400 Bad Request", "unparseable request line")
        credential = _basic_credential(head)
        grant = self.authorize(*credential) if credential else None
        if grant is None:
            raise Refused("407 Proxy Authentication Required", f"{method} {target}: no valid credential")
        who = grant.label
        if method != "CONNECT":
            raise Refused("405 Method Not Allowed", f"{method} {target}: only CONNECT", who)
        try:
            host, port = parse_connect_target(target)
        except ValueError as e:
            raise Refused("400 Bad Request", f"{target}: {e}", who)
        if port != PORT:
            raise Refused("403 Forbidden", f"{host}:{port}: port 443 only", who)
        try:
            ipaddress.ip_address(host)
            raise Refused("403 Forbidden", f"{host}: IP literals aren't allowed", who)
        except ValueError:
            pass
        if not grant.open_internet and host not in grant.allow:
            raise Refused("403 Forbidden", f"{host}: not on this scope's allowlist", who)
        return grant, host

    async def _resolve_checked(self, host: str) -> List[str]:
        try:
            ips = await asyncio.wait_for(self.resolve(host), WORKSHOP_PROXY_HANDSHAKE_SECONDS)
        except (OSError, asyncio.TimeoutError) as e:
            raise Refused("502 Bad Gateway", f"{host}: lookup failed ({e})")
        if not ips or not all(address_ok(ip) for ip in ips):
            raise Refused("403 Forbidden", f"{host} -> {ips}: not every address is public")
        return ips

    async def _open_upstream(self, host: str, ips: List[str]):
        for ip in ips:
            try:
                r, w = await asyncio.wait_for(self.connect(ip, PORT), WORKSHOP_PROXY_HANDSHAKE_SECONDS)
                return r, w, ip
            except (OSError, asyncio.TimeoutError):
                logger.debug(f"egress {host}: {ip} didn't answer")
        return None


async def _relay(a_reader, a_writer, b_reader, b_writer,
                 idle: float = WORKSHOP_PROXY_IDLE_SECONDS) -> None:
    """Pump both directions until either side closes or neither has moved a
    byte for `idle` seconds (a long one-way download keeps it alive)."""
    loop = asyncio.get_running_loop()
    last = [loop.time()]

    async def pump(reader, writer):
        while True:
            try:
                data = await asyncio.wait_for(reader.read(65536), idle)
            except asyncio.TimeoutError:
                if loop.time() - last[0] >= idle:
                    return
                continue
            if not data:
                return
            last[0] = loop.time()
            writer.write(data)
            await writer.drain()

    tasks = [asyncio.ensure_future(pump(a_reader, b_writer)),
             asyncio.ensure_future(pump(b_reader, a_writer))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for w in (a_writer, b_writer):
            w.close()
