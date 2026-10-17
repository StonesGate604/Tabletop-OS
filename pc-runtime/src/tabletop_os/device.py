"""Device contract and a persistent TCP/UDP adapter. I/O never owns game state."""
from collections import deque
import json
import logging
import queue
import secrets
import select
import socket
import threading
import time
import uuid

from .config import NetworkConfig
from .frame_protocol import encode_frame
from .model import DeviceSnapshot, snapshot_from_message

LOG = logging.getLogger(__name__)


class NetworkDevice:
    def __init__(self, config: NetworkConfig):
        self.config = config
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._outgoing = queue.Queue(maxsize=32)
        self._events = deque(maxlen=128)
        self._snapshot = DeviceSnapshot()
        self._display = {}
        self._frame = None
        self._connected = False
        self._last_runtime_tick = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="device-io", daemon=True)

    def start(self):
        self._thread.start()

    def request(self, kind: str, **fields) -> str:
        request_id = uuid.uuid4().hex
        with self._lock:
            if not self._connected:
                raise RuntimeError("DEVICE_DISCONNECTED")
            try:
                self._outgoing.put_nowait({"v": 1, "type": kind, "request_id": request_id, **fields})
            except queue.Full as exc:
                raise RuntimeError("COMMAND_QUEUE_FULL") from exc
        return request_id

    def publish(self, rgb: bytes):
        with self._lock:
            self._frame = rgb  # one latest slot; old frames are never replayed
            self._last_runtime_tick = time.monotonic()

    def snapshot(self) -> DeviceSnapshot:
        with self._lock:
            return self._snapshot

    def display_status(self) -> dict:
        with self._lock:
            return dict(self._display)

    def poll(self) -> list[dict]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def _emit(self, message: dict):
        with self._lock:
            if len(self._events) == self._events.maxlen:
                raise RuntimeError("DEVICE_EVENT_QUEUE_FULL")
            self._events.append(message)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            LOG.error("Device worker did not exit within deadline")

    def _run(self):
        cfg = self.config
        while not self._stop.is_set():
            tcp = udp = None
            try:
                with self._lock:
                    runtime_age = time.monotonic()-self._last_runtime_tick
                if runtime_age > cfg.response_timeout_ms/1000:
                    self._stop.wait(.1)
                    continue
                tcp = socket.create_connection((cfg.host, cfg.control_port), timeout=1)
                tcp.settimeout(0.2)
                tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp.connect((cfg.host, cfg.frame_port))
                udp.setblocking(False)
                with self._lock:
                    self._connected = True
                LOG.info("Control connected to %s:%s", cfg.host, cfg.control_port)
                self._session_loop(tcp, udp)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                LOG.warning("Device session lost: %s", exc)
            finally:
                if tcp:
                    try:
                        self._write(tcp, {"v": 1, "type": "motion.stop", "request_id": "shutdown-stop"})
                    except OSError:
                        pass
                    tcp.close()
                if udp:
                    udp.close()
                with self._lock:
                    self._connected = False
                    self._snapshot = DeviceSnapshot()
                    self._display = {}
                    self._frame = None
                    self._events.clear()
                    self._events.append({"v": 1, "type": "connection.lost"})
                    while not self._outgoing.empty():
                        self._outgoing.get_nowait()
            self._stop.wait(cfg.reconnect_ms/1000)

    @staticmethod
    def _write(tcp: socket.socket, message: dict):
        tcp.sendall((json.dumps(message, separators=(",", ":"), allow_nan=False)+"\n").encode("utf-8"))

    def _session_loop(self, tcp: socket.socket, udp: socket.socket):
        cfg = self.config
        now = time.monotonic()
        last_heartbeat = last_status = 0.0
        last_ack = now
        session, frame_id = secrets.randbelow(0xFFFFFFFF)+1, 0
        display_requested = display_ready = False
        incoming = bytearray()
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                runtime_age = now-self._last_runtime_tick
            if runtime_age > cfg.response_timeout_ms/1000:
                raise RuntimeError("RUNTIME_STALLED")
            if now-last_ack > cfg.response_timeout_ms/1000:
                raise RuntimeError("HEARTBEAT_RESPONSE_TIMEOUT")
            if now-last_heartbeat >= cfg.heartbeat_ms/1000:
                self._write(tcp, {"v": 1, "type": "heartbeat", "request_id": "lease"})
                last_heartbeat = now
            if now-last_status >= cfg.heartbeat_ms/1000:
                self._write(tcp, {"v": 1, "type": "status.get", "request_id": "snapshot"})
                last_status = now
            try:
                while True:
                    self._write(tcp, self._outgoing.get_nowait())
            except queue.Empty:
                pass
            readable, _, _ = select.select([tcp], [], [], 0.005)
            if readable:
                data = tcp.recv(4096)
                if not data:
                    raise RuntimeError("CONTROL_EOF")
                incoming.extend(data)
                while b"\n" in incoming:
                    line, _, remaining = incoming.partition(b"\n")
                    incoming = bytearray(remaining)
                    if len(line) > 4096:
                        raise ValueError("CONTROL_LINE_TOO_LARGE")
                    message = json.loads(line)
                    if not isinstance(message, dict) or message.get("v") != 1:
                        raise ValueError("INVALID_DEVICE_MESSAGE")
                    kind = message.get("type")
                    if kind == "heartbeat.ack":
                        last_ack = now
                        if not display_requested:
                            self._write(tcp, {"v": 1, "type": "display.open", "request_id": "display", "session": session})
                            display_requested = True
                    elif kind == "display.opened" and message.get("session") == session:
                        display_ready = True
                    elif kind in ("status", "pong"):
                        status = snapshot_from_message(message, now)
                        with self._lock:
                            self._snapshot = status
                            self._display = message.get("display", {})
                    else:
                        self._emit(message)
                        if kind == "control.lease_expired":
                            raise RuntimeError("CONTROL_LEASE_EXPIRED")
                if len(incoming) > 4096:
                    raise ValueError("CONTROL_LINE_TOO_LARGE")
            with self._lock:
                if display_ready:
                    rgb, self._frame = self._frame, None
                else:
                    rgb = None
            if rgb is not None and display_ready:
                frame_id = (frame_id+1) & 0xFFFFFFFF
                for packet in encode_frame(rgb, session, frame_id):
                    try:
                        udp.send(packet)
                    except BlockingIOError:
                        break  # incomplete frame expires; never retry old pixels
