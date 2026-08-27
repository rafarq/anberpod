"""Nonblocking reader for the physical controls on ``/dev/input/eventN``.

Mirrors the proven implementation in the sibling ``radio`` project
(same RG35XX H device/firmware): raw ``struct input_event`` decoding is
a pure function of bytes and is unit tested directly on desktop.
:class:`InputReader` only adds opening the device node ``O_NONBLOCK``
and draining whatever bytes are currently available without ever
blocking the main frame loop.
"""

from __future__ import annotations

import os
import struct
from typing import List, Tuple

from anberpod.input.codes import EV_ABS, EV_KEY, ControlEvent, normalize

# 64-bit `struct input_event`: two 8-byte timeval fields, then
# type/code (u16 each) and value (s32). 24 bytes total.
_EVENT_FORMAT = "qqHHi"
EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)

DEFAULT_DEVICE_PATH = "/dev/input/event1"


def decode_chunk(buffer: bytes) -> Tuple[List[ControlEvent], bytes]:
    """Decode as many whole events as ``buffer`` contains.

    Returns ``(events, leftover)`` where ``leftover`` is the trailing
    partial-event bytes to prepend to the next chunk read from the
    device.
    """
    events: List[ControlEvent] = []
    offset = 0
    while offset + EVENT_SIZE <= len(buffer):
        raw = buffer[offset : offset + EVENT_SIZE]
        offset += EVENT_SIZE
        _sec, _usec, ev_type, ev_code, ev_value = struct.unpack(_EVENT_FORMAT, raw)
        if ev_type not in (EV_KEY, EV_ABS):
            continue
        decoded = normalize(ev_code, ev_value)
        if decoded is not None:
            events.append(decoded)
    return events, buffer[offset:]


class InputReader:
    """Owns the open device fd and buffers partial reads across polls."""

    def __init__(self, device_path: str = DEFAULT_DEVICE_PATH):
        self.device_path = device_path
        self._fd: "int | None" = None
        self._buffer = b""

    def open(self) -> None:
        self._fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def poll(self) -> List[ControlEvent]:
        """Drain all events currently available; never blocks."""
        if self._fd is None:
            return []

        all_events: List[ControlEvent] = []
        while True:
            try:
                chunk = os.read(self._fd, EVENT_SIZE * 64)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            events, self._buffer = decode_chunk(self._buffer + chunk)
            all_events.extend(events)
            if len(chunk) < EVENT_SIZE * 64:
                break
        return all_events

    def __enter__(self) -> "InputReader":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
