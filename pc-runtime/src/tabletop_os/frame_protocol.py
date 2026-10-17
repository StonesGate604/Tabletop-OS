"""TOSF v1, fixed 64x64 RGB888, bounded latest-frame UDP assembly."""
import math
import struct
import zlib

MAGIC = b"TOSF"
VERSION = 1
WIDTH = HEIGHT = 64
FRAME_BYTES = WIDTH * HEIGHT * 3
CHUNK_BYTES = 1200
CHUNKS = math.ceil(FRAME_BYTES / CHUNK_BYTES)
# magic, version, reserved, width, height, session, frame, index, count, CRC32
HEADER = struct.Struct("!4sBBHHIIHHI")


def newer32(candidate: int, previous: int) -> bool:
    return 0 < ((candidate - previous) & 0xFFFFFFFF) < 0x80000000


def encode_frame(rgb: bytes, session: int, frame_id: int) -> list[bytes]:
    if len(rgb) != FRAME_BYTES or not 0 < session <= 0xFFFFFFFF or not 0 <= frame_id <= 0xFFFFFFFF:
        raise ValueError("Invalid RGB frame, session or frame id")
    crc = zlib.crc32(rgb)
    return [HEADER.pack(MAGIC, VERSION, 0, WIDTH, HEIGHT, session, frame_id,
                        index, CHUNKS, crc) + rgb[index*CHUNK_BYTES:(index+1)*CHUNK_BYTES]
            for index in range(CHUNKS)]


class FrameAssembler:
    """Reference decoder for simulator/tests; Pi has a matching C++ decoder."""
    def __init__(self, timeout_s: float = 0.1):
        self.timeout_s = timeout_s
        self.session = 0
        self.frame_id = None
        self.started = 0.0
        self.parts = {}
        self.crc = 0
        self.finished = False

    def set_session(self, session: int):
        self.session = session
        self.frame_id = None
        self.parts.clear()
        self.finished = False

    def feed(self, packet: bytes, now: float) -> bytes | None:
        if len(packet) < HEADER.size:
            return None
        magic, version, flags, width, height, session, frame_id, index, count, crc = HEADER.unpack_from(packet)
        expected = min(CHUNK_BYTES, FRAME_BYTES-index*CHUNK_BYTES)
        if (magic != MAGIC or version != VERSION or flags != 0 or
                (width, height) != (WIDTH, HEIGHT) or not self.session or session != self.session or
                count != CHUNKS or index >= CHUNKS or len(packet) != HEADER.size+expected):
            return None
        if self.frame_id is None or newer32(frame_id, self.frame_id):
            self.frame_id, self.started, self.crc = frame_id, now, crc
            self.parts.clear()
            self.finished = False
        if frame_id != self.frame_id or self.finished or crc != self.crc:
            return None
        if now-self.started > self.timeout_s:
            self.finished = True
            self.parts.clear()
            return None
        payload = packet[HEADER.size:]
        if index in self.parts and self.parts[index] != payload:
            self.finished = True
            self.parts.clear()
            return None
        self.parts[index] = payload
        if len(self.parts) != CHUNKS:
            return None
        rgb = b"".join(self.parts[i] for i in range(CHUNKS))
        self.finished = True
        self.parts.clear()
        return rgb if zlib.crc32(rgb) == self.crc else None
