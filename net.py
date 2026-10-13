"""Two-player local-network play for Daleks in Hedges (standard library only).

Discovery: the host broadcasts a small JSON beacon on UDP DISCOVERY_PORT once a
second; a joining game listens there and lists the hosts it hears.

Play: one non-blocking UDP socket per game. Each side sends its state about
STATE_HZ times a second, numbered so stale (out-of-order) packets are dropped.
Events that must arrive (exterminations, the TARDIS, level changes ...) are
numbered too, resent every RESEND_MS until acknowledged, and de-duplicated.
"""

import json
import random
import socket
import time

GAME_ID = "dih"
PROTOCOL = 1
DISCOVERY_PORT = 47474
GAME_PORT = 47475
BEACON_S = 1.0
SCAN_S = 3.0
STATE_HZ = 20
RESEND_S = 0.1
TIMEOUT_S = 3.0
MAX_PACKET = 8192


def _clock() -> float:
    return time.monotonic()


def local_ip() -> str:
    """Best guess at this machine's LAN address (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def host_label() -> str:
    """Short machine name shown in the lobby list."""
    name = socket.gethostname().split(".")[0] or "Player"
    return name[:20]


def parse_hostport(text: str, default_port: int = GAME_PORT):
    """'192.168.1.20' or '192.168.1.20:47480' -> (ip, port)."""
    host, sep, port = text.strip().partition(":")
    return socket.gethostbyname(host), int(port) if sep and port else default_port


def _encode(msg) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


def _decode(data: bytes):
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return msg if isinstance(msg, dict) else None


def _reuse(sock):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):  # several listeners on one machine (testing)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass


class Beacon:
    """Host side: 'a game is here' once a second on the discovery port."""

    def __init__(self, info: dict, port: int = DISCOVERY_PORT):
        self.info = dict(info, id=GAME_ID, v=PROTOCOL)
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setblocking(False)
        self.next_t = 0.0
        self.sent = 0

    def tick(self, **changes):
        self.info.update(changes)
        t = _clock()
        if t < self.next_t:
            return
        self.next_t = t + BEACON_S
        data = _encode(self.info)
        # The broadcast reaches the LAN; the loopback copy lets two games on
        # one machine find each other even where broadcast is filtered.
        for addr in (("255.255.255.255", self.port), ("127.0.0.1", self.port)):
            try:
                self.sock.sendto(data, addr)
                self.sent += 1
            except OSError:
                pass

    def close(self):
        self.sock.close()


class Scanner:
    """Join side: listen on the discovery port and collect hosts."""

    def __init__(self, port: int = DISCOVERY_PORT, seconds: float = SCAN_S):
        self.t0 = _clock()
        self.seconds = seconds
        self.found = {}  # (ip, game port) -> info
        self.error = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _reuse(self.sock)
        try:
            self.sock.bind(("", port))
        except OSError as exc:
            self.error = f"Can't listen on UDP {port}: {exc.strerror or exc}"
        self.sock.setblocking(False)

    def poll(self):
        if self.error:
            return
        while True:
            try:
                data, (ip, _port) = self.sock.recvfrom(MAX_PACKET)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            msg = _decode(data)
            if not msg or msg.get("id") != GAME_ID or msg.get("v") != PROTOCOL:
                continue
            try:
                game_port = int(msg.get("port", GAME_PORT))
            except (TypeError, ValueError):
                continue
            # The broadcast copy and the loopback copy of one beacon are the same
            # game: one entry per (reported address, port), LAN source preferred.
            key = (str(msg.get("ip", ip)), game_port)
            old = self.found.get(key)
            if old is not None and not old["addr"][0].startswith("127.") and ip.startswith("127."):
                old["seen"] = _clock()
                continue
            msg["addr"] = (ip, game_port)
            msg["seen"] = _clock()
            self.found[key] = msg

    def done(self) -> bool:
        return _clock() - self.t0 >= self.seconds

    def hosts(self):
        """[(address to join, beacon info)], by name."""
        return sorted(((m["addr"], m) for m in self.found.values()), key=lambda am: (am[1].get("name", ""), am[0]))

    def close(self):
        self.sock.close()


class Link:
    """The play connection: latest-state packets plus reliable events."""

    def __init__(self, role: str, port: int = 0, peer=None):
        self.role = role                 # "host" or "join"
        self.peer = peer                 # (ip, port); the host learns it from the first packet
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", port))
        self.sock.setblocking(False)
        self.port = self.sock.getsockname()[1]
        self.out_seq = 0
        self.in_seq = -1
        self.ev_seq = 0
        self.unacked = {}                # event id -> [message, last send time]
        self.seen_events = set()
        self.events = []                 # (kind, data) received, oldest first
        self.state = None                # latest peer state
        self.state_fresh = False
        self.last_recv = None            # clock of the last packet from the peer
        self.next_state_t = 0.0
        self.drop_rate = 0.0             # testing: lose this fraction of outgoing packets
        self.blocked = False             # testing: drop everything (a pulled cable)
        self.stats = {"sent": 0, "recv": 0, "stale": 0, "dup": 0, "resent": 0}

    # ----- sending --------------------------------------------------------
    def _send(self, msg):
        if self.peer is None or self.blocked:
            return
        if self.drop_rate and random.random() < self.drop_rate:
            return
        try:
            self.sock.sendto(_encode(msg), self.peer)
            self.stats["sent"] += 1
        except OSError:
            pass

    def state_due(self) -> bool:
        t = _clock()
        if t < self.next_state_t:
            return False
        self.next_state_t = t + 1.0 / STATE_HZ
        return True

    def send_state(self, payload: dict):
        self.out_seq += 1
        self._send({"t": "s", "q": self.out_seq, "d": payload})

    def send_event(self, kind: str, **data):
        self.ev_seq += 1
        msg = {"t": "e", "i": self.ev_seq, "k": kind, "d": data}
        self.unacked[self.ev_seq] = [msg, _clock()]
        self._send(msg)

    # ----- receiving ------------------------------------------------------
    def poll(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(MAX_PACKET)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:  # e.g. ICMP port unreachable surfaced on Windows/Linux
                break
            if self.blocked:
                continue
            msg = _decode(data)
            if msg is None:
                continue
            if self.peer is None and self.role == "host" and msg.get("t") == "e" and msg.get("k") == "hello":
                self.peer = addr
            if addr != self.peer:
                continue  # someone else's packet
            self.last_recv = _clock()
            self.stats["recv"] += 1
            kind = msg.get("t")
            if kind == "s":
                q = msg.get("q", -1)
                if q <= self.in_seq:
                    self.stats["stale"] += 1  # out of order: a newer state already arrived
                    continue
                self.in_seq = q
                self.state = msg.get("d")
                self.state_fresh = True
            elif kind == "e":
                eid = msg.get("i")
                self._send({"t": "a", "i": eid})
                if eid in self.seen_events:
                    self.stats["dup"] += 1
                    continue
                self.seen_events.add(eid)
                self.events.append((msg.get("k"), msg.get("d") or {}))
            elif kind == "a":
                self.unacked.pop(msg.get("i"), None)
        t = _clock()
        for entry in self.unacked.values():
            if t - entry[1] >= RESEND_S:
                entry[1] = t
                self.stats["resent"] += 1
                self._send(entry[0])

    def take_events(self):
        events, self.events = self.events, []
        return events

    def connected(self) -> bool:
        return self.last_recv is not None

    def lost(self) -> bool:
        return self.last_recv is not None and _clock() - self.last_recv > TIMEOUT_S

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
