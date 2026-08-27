"""Button/axis code mapping and raw-value normalization for /dev/input/event1.

Codes confirmed on this exact RG35XX H unit (see the sibling ``radio``
project's ``radio/input/codes.py``, captured from a real event trace on
the same device/firmware):

    304 A, 305 B, 306 Y, 307 X, 308 L1, 309 R1,
    17 DY, 16 DX, 310 SELECT, 311 START, 312 MENU
    114 VOLUME_DOWN, 115 VOLUME_UP

``EV_KEY`` (buttons) reports 0=release, 1=press, 2=autorepeat (while
held). ``EV_ABS`` (the D-pad, exposed as a hat axis) reports -1/0/1 in
the sample trace; a raw value of ``2`` for a still-held direction is
normalized to ``-1``, matching the confirmed sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

EV_KEY = 0x01
EV_ABS = 0x03

BUTTON_CODES = {
    304: "A",
    305: "B",
    306: "Y",
    307: "X",
    308: "L1",
    309: "R1",
    310: "SELECT",
    311: "START",
    312: "MENU",
    114: "VOLUME_DOWN",
    115: "VOLUME_UP",
}

AXIS_CODES = {
    16: "DX",
    17: "DY",
}


@dataclass(frozen=True)
class ControlEvent:
    """A decoded, normalized physical-control event."""

    name: str  # "A", "B", ..., "DX", "DY"
    kind: str  # "button" or "axis"
    value: int  # normalized value (button: 0/1, axis: -1/0/1)
    pressed: bool  # True while a button is down (press or repeat)
    repeat: bool  # True only when the raw value was the autorepeat marker (2)


def normalize(code: int, raw_value: int) -> Optional[ControlEvent]:
    """Map one raw ``(code, value)`` pair to a :class:`ControlEvent`.

    Returns ``None`` for codes outside the confirmed mapping (ignored).
    """
    if code in BUTTON_CODES:
        name = BUTTON_CODES[code]
        if raw_value == 0:
            return ControlEvent(name, "button", 0, pressed=False, repeat=False)
        if raw_value == 1:
            return ControlEvent(name, "button", 1, pressed=True, repeat=False)
        if raw_value == 2:
            return ControlEvent(name, "button", 1, pressed=True, repeat=True)
        return None

    if code in AXIS_CODES:
        name = AXIS_CODES[code]
        is_repeat = raw_value == 2
        value = -1 if is_repeat else raw_value
        return ControlEvent(name, "axis", value, pressed=value != 0, repeat=is_repeat)

    return None
