import struct

from anberpod.input.codes import ControlEvent, normalize
from anberpod.input.reader import InputReader, decode_chunk

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03


def _pack(ev_type, code, value):
    return struct.pack("qqHHi", 0, 0, ev_type, code, value)


def test_normalize_button_press_release_repeat():
    assert normalize(304, 1) == ControlEvent("A", "button", 1, pressed=True, repeat=False)
    assert normalize(304, 0) == ControlEvent("A", "button", 0, pressed=False, repeat=False)
    assert normalize(304, 2) == ControlEvent("A", "button", 1, pressed=True, repeat=True)


def test_normalize_maps_all_confirmed_button_codes():
    expected = {
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
    for code, name in expected.items():
        event = normalize(code, 1)
        assert event.name == name
        assert event.kind == "button"


def test_normalize_axis_value_two_behaves_like_negative_one():
    assert normalize(16, 2) == ControlEvent("DX", "axis", -1, pressed=True, repeat=True)
    assert normalize(17, -1) == ControlEvent("DY", "axis", -1, pressed=True, repeat=False)
    assert normalize(17, 1) == ControlEvent("DY", "axis", 1, pressed=True, repeat=False)
    assert normalize(16, 0) == ControlEvent("DX", "axis", 0, pressed=False, repeat=False)


def test_normalize_unknown_code_returns_none():
    assert normalize(999, 1) is None


def test_decode_chunk_full_mapping_in_order():
    buf = b"".join(_pack(EV_KEY, code, 1) for code in (304, 305, 306, 307, 308, 309, 310, 311, 312))
    events, leftover = decode_chunk(buf)
    assert [e.name for e in events] == ["A", "B", "Y", "X", "L1", "R1", "SELECT", "START", "MENU"]
    assert leftover == b""


def test_decode_chunk_ignores_non_key_non_abs_types():
    buf = _pack(EV_SYN, 0, 0) + _pack(EV_KEY, 304, 1)
    events, leftover = decode_chunk(buf)
    assert len(events) == 1
    assert events[0].name == "A"
    assert leftover == b""


def test_decode_chunk_keeps_partial_trailing_bytes_for_next_read():
    full = _pack(EV_KEY, 304, 1)
    partial = full[:10]
    events, leftover = decode_chunk(full + partial)
    assert len(events) == 1
    assert leftover == partial


def test_decode_chunk_axis_codes_dx_dy():
    buf = _pack(EV_ABS, 16, -1) + _pack(EV_ABS, 17, 1)
    events, _ = decode_chunk(buf)
    assert [(e.name, e.value) for e in events] == [("DX", -1), ("DY", 1)]


def test_input_reader_poll_without_open_returns_empty_nonblocking():
    reader = InputReader("/dev/null")
    assert reader.poll() == []
    assert reader.is_open is False


def test_input_reader_open_close_real_device_node(tmp_path):
    fifo_like = tmp_path / "fake-event"
    fifo_like.write_bytes(_pack(EV_KEY, 304, 1))
    reader = InputReader(str(fifo_like))
    reader.open()
    try:
        assert reader.is_open is True
        events = reader.poll()
        assert [e.name for e in events] == ["A"]
    finally:
        reader.close()
    assert reader.is_open is False


def test_button_mapping_includes_x_delete():
    from anberpod.domain.models import InputAction
    from anberpod.ui.loop import _BUTTON_ACTIONS

    assert _BUTTON_ACTIONS["X"] == InputAction.DELETE
    assert InputAction.DELETE.value == "delete"
