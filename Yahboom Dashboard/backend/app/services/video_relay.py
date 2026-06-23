"""
MJPEG relay: one HTTP connection to the Pi upstream, fan-out to many dashboard clients.

Browsers use GET /api/video_feed on this backend; only the ingest thread talks to the Pi.
"""

from __future__ import annotations

import queue
import threading
import time
import urllib.error
import urllib.request

# Re-emitted multipart boundary (clients need not match the Pi's boundary).
_BOUNDARY = b"--frame"
_PART_PREFIX = b"Content-Type: image/jpeg\r\n\r\n"


def _format_mjpeg_part(jpeg: bytes) -> bytes:
    return _BOUNDARY + b"\r\n" + _PART_PREFIX + jpeg + b"\r\n"


def _iter_jpeg_frames(byte_stream, chunk_size: int = 8192):
    """Extract consecutive JPEG images from an HTTP byte stream."""
    buf = b""
    while True:
        chunk = byte_stream.read(chunk_size)
        if not chunk:
            break
        buf += chunk
        while True:
            start = buf.find(b"\xff\xd8")
            if start == -1:
                if len(buf) > 1_000_000:
                    buf = b""
                break
            end = buf.find(b"\xff\xd9", start + 2)
            if end == -1:
                buf = buf[start:]
                break
            yield buf[start : end + 2]
            buf = buf[end + 2 :]


class VideoRelay:
    """Singleton-style relay from one upstream MJPEG URL to many subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._upstream: str | None = None
        self._ingest_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._subscribers: list[queue.Queue[bytes]] = []
        self._last_part: bytes | None = None

    def is_active(self) -> bool:
        with self._lock:
            return self._upstream is not None and not self._stop_event.is_set()

    def start(self, upstream_url: str) -> None:
        with self._lock:
            if self._upstream == upstream_url and self._ingest_thread and self._ingest_thread.is_alive():
                return
            self._stop_locked()
            self._upstream = upstream_url
            self._stop_event.clear()
            self._ingest_thread = threading.Thread(
                target=self._ingest_loop,
                name="video-relay-ingest",
                daemon=True,
            )
            self._ingest_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop_event.set()
        self._upstream = None
        self._last_part = None
        self._subscribers.clear()

    def _broadcast(self, part: bytes) -> None:
        self._last_part = part
        for q in list(self._subscribers):
            try:
                q.put_nowait(part)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(part)
                except queue.Empty:
                    pass

    def subscribe(self):
        """Flask streaming generator — one subscriber connection."""
        q: queue.Queue[bytes] = queue.Queue(maxsize=4)
        with self._lock:
            if self._last_part is not None:
                try:
                    q.put_nowait(self._last_part)
                except queue.Full:
                    pass
            self._subscribers.append(q)
        try:
            while not self._stop_event.is_set():
                try:
                    yield q.get(timeout=1.0)
                except queue.Empty:
                    if not self.is_active():
                        break
        finally:
            with self._lock:
                if q in self._subscribers:
                    self._subscribers.remove(q)

    def _ingest_loop(self) -> None:
        while not self._stop_event.is_set():
            url = self._upstream
            if not url:
                break
            try:
                response = urllib.request.urlopen(url, timeout=15)
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(1.0)
                continue
            try:
                for jpeg in _iter_jpeg_frames(response):
                    if self._stop_event.is_set():
                        break
                    self._broadcast(_format_mjpeg_part(jpeg))
            finally:
                response.close()
            if not self._stop_event.is_set():
                time.sleep(0.5)


video_relay = VideoRelay()
