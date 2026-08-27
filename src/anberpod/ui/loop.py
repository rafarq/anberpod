"""SDL2 fullscreen frame loop entrypoint (640x480, PySDL2 + Pillow).

SDL2 and Pillow are only ever imported inside :func:`run` — never at
module scope — so importing ``anberpod.ui.loop`` itself stays cheap and
doesn't require a display or these packages to be installed. Only the
real device (or a machine with PySDL2/Pillow set up) can actually call
:func:`run`; host tests exercise :mod:`anberpod.app` and
:mod:`anberpod.input` directly instead.

Input reading, UI redraw and playback-subprocess polling all happen in
this single loop each frame: none of the three ever blocks on the
others, since ``InputReader.poll()`` and ``Application.poll_playback()``
are both non-blocking and rendering is pure CPU work against
already-known state.

Window creation mirrors the approach proven on-device by the sibling
``radio`` project on this same RG35XX H unit: ``SDL_CreateWindow`` is
called with ``width=0, height=0`` and
``SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_SHOWN`` (letting SDL pick
the desktop's actual size/mode) instead of an explicit-size fullscreen
mode switch, which fails on this device's GLES/EGL setup with
``SDL_CreateWindow failed: Could not initialize EGL``. The 640x480
logical Pillow render surface is still preserved via
``SDL_RenderSetLogicalSize``.
"""

from __future__ import annotations

import logging
import time

from anberpod.app import Application
from anberpod.domain.models import InputAction, InputEvent
from anberpod.input.reader import InputReader

WIDTH = 640
HEIGHT = 480
FRAME_INTERVAL = 1.0 / 30.0
INPUT_REPEAT_ACTIONS = {InputAction.UP, InputAction.DOWN, InputAction.LEFT, InputAction.RIGHT}

logger = logging.getLogger(__name__)

_AXIS_ACTIONS = {
    ("DY", -1): InputAction.UP,
    ("DY", 1): InputAction.DOWN,
    ("DX", -1): InputAction.LEFT,
    ("DX", 1): InputAction.RIGHT,
}

_BUTTON_ACTIONS = {
    "A": InputAction.ACCEPT,
    "B": InputAction.BACK,
    "MENU": InputAction.MENU,
    "START": InputAction.MENU,
}


def run(app: Application, input_reader: InputReader) -> None:
    """Run the fullscreen SDL2 UI loop until the app requests exit."""
    try:
        logger.info("importing sdl2")
        import sdl2

        logger.info("sdl2 imported: version=%s", getattr(sdl2, "__version__", "?"))

        logger.info("importing Pillow via anberpod.ui.renderer")
        from anberpod.ui.renderer import Renderer

        logger.info("Pillow/renderer imported")

        renderer_model = Renderer(artwork_root=app.paths.cache / "artwork")

        logger.info("SDL_Init(SDL_INIT_VIDEO) attempt")
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {sdl2.SDL_GetError()}")
        logger.info("SDL_Init: ok")

        try:
            logger.info("SDL_CreateWindow attempt (fullscreen-desktop, size=auto)")
            window = sdl2.SDL_CreateWindow(
                b"AnberPod",
                sdl2.SDL_WINDOWPOS_UNDEFINED,
                sdl2.SDL_WINDOWPOS_UNDEFINED,
                0,
                0,
                sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP | sdl2.SDL_WINDOW_SHOWN,
            )
            if not window:
                raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
            logger.info("SDL_CreateWindow: ok")

            try:
                logger.info("SDL_CreateRenderer attempt (accelerated)")
                sdl_renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_ACCELERATED)
                if not sdl_renderer:
                    logger.warning(
                        "Accelerated SDL renderer unavailable (%s); falling back to software",
                        sdl2.SDL_GetError(),
                    )
                    sdl_renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_SOFTWARE)
                if not sdl_renderer:
                    raise RuntimeError(f"SDL_CreateRenderer failed: {sdl2.SDL_GetError()}")
                logger.info("SDL_CreateRenderer: ok")

                try:
                    sdl2.SDL_RenderSetLogicalSize(sdl_renderer, WIDTH, HEIGHT)
                    logger.info("SDL_RenderSetLogicalSize: width=%d height=%d", WIDTH, HEIGHT)

                    texture = sdl2.SDL_CreateTexture(
                        sdl_renderer,
                        sdl2.SDL_PIXELFORMAT_RGB24,
                        sdl2.SDL_TEXTUREACCESS_STREAMING,
                        WIDTH,
                        HEIGHT,
                    )
                    if not texture:
                        raise RuntimeError(f"SDL_CreateTexture failed: {sdl2.SDL_GetError()}")
                    logger.info("SDL_CreateTexture: ok")

                    try:
                        logger.info("input_reader.open attempt: device=%s", input_reader.device_path)
                        try:
                            input_reader.open()
                            logger.info("input_reader.open: ok")
                        except OSError as exc:
                            logger.warning("could not open input device: %s", exc)

                        try:
                            logger.info("main loop enter")
                            _loop(app, input_reader, sdl2, sdl_renderer, texture, renderer_model)
                            logger.info("main loop exit: exit_requested=%s", app.state.exit_requested)
                        finally:
                            logger.info("cleanup: input_reader.close")
                            input_reader.close()
                            logger.info("cleanup: app.playback.shutdown")
                            app.playback.shutdown()
                    finally:
                        logger.info("cleanup: SDL_DestroyTexture")
                        sdl2.SDL_DestroyTexture(texture)
                finally:
                    logger.info("cleanup: SDL_DestroyRenderer")
                    sdl2.SDL_DestroyRenderer(sdl_renderer)
            finally:
                logger.info("cleanup: SDL_DestroyWindow")
                sdl2.SDL_DestroyWindow(window)
        finally:
            logger.info("cleanup: SDL_Quit")
            sdl2.SDL_Quit()
    except Exception:
        logger.exception("unhandled exception in ui.loop.run")
        raise


def _loop(app: Application, input_reader: InputReader, sdl2, sdl_renderer, texture, renderer_model) -> None:
    event = sdl2.SDL_Event()
    while not app.state.exit_requested:
        frame_start = time.monotonic()

        while sdl2.SDL_PollEvent(event) != 0:
            if event.type == sdl2.SDL_QUIT:
                app.state.exit_requested = True

        for control_event in input_reader.poll():
            action = None
            if control_event.kind == "button":
                action = _BUTTON_ACTIONS.get(control_event.name)
                if action is None or not control_event.pressed:
                    continue
            else:
                if control_event.value == 0:
                    continue
                action = _AXIS_ACTIONS.get((control_event.name, control_event.value))
            if action is not None:
                app.handle(InputEvent(action, repeated=control_event.repeat))

        for _failure in app.poll_playback():
            pass

        screen = app.screen()
        from anberpod.ui.state import Route

        player = app.player_view() if screen.route is Route.PLAYER else None
        if player is not None:
            frame = renderer_model.render_player(player, app.t)
        else:
            frame = renderer_model.render(screen, app.t)
        pixels = frame.tobytes("raw", "RGB")
        sdl2.SDL_UpdateTexture(texture, None, pixels, WIDTH * 3)
        sdl2.SDL_RenderClear(sdl_renderer)
        sdl2.SDL_RenderCopy(sdl_renderer, texture, None, None)
        sdl2.SDL_RenderPresent(sdl_renderer)

        elapsed = time.monotonic() - frame_start
        remaining = FRAME_INTERVAL - elapsed
        if remaining > 0:
            time.sleep(remaining)
