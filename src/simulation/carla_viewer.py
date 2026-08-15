"""A window for watching a CARLA run, with a real 2D HUD.

CARLA's debug drawing is world-space only. There is no screen-space call
anywhere in its Python API — ``draw_hud_*`` takes ``Location`` arguments in
metres, "HUD" naming the render layer rather than the coordinate system, and
there is no ``draw_hud_string`` at all. Text pinned in front of the spectator
approximates a HUD and never quite becomes one: it turns with the camera, it
is occluded by anything between, and its size is a compromise.

So this does what CARLA's own ``manual_control.py`` does — renders a camera
sensor into a client-side window and draws the HUD on top of it in 2D, where
text is text and a corner is a corner.

**It is a viewer, not a participant.** It never advances the world, never
touches the bus, and a closed window only stops the drawing. Nothing it does
can change a result.

The server still has to render for a camera sensor to produce anything, but
it no longer has to render *for you*: with this window open the server can be
launched ``-RenderOffScreen``, which is usually faster than watching its own
viewport.
"""

from collections.abc import Sequence

import numpy as np

import carla

# Chase camera placement, relative to the bus.
CAMERA_BEHIND_M = 8.0
CAMERA_ABOVE_M = 3.4
CAMERA_PITCH_DEG = -12.0

# How often the sensor is asked for a frame, in simulated seconds. The world
# runs many times faster than real time, so capturing every tick renders
# frames nobody will ever see.
SENSOR_TICK_S = 0.1

# Wall-clock ceiling on redraws. Blitting faster than this is work the eye
# cannot use, and the simulation is what we would rather spend the time on.
MAX_FPS = 30

# HUD layout, in pixels.
HUD_MARGIN = 18
HUD_LINE_H = 30
HUD_FONT_PT = 26
HUD_PAD = 12
HUD_INK = (238, 238, 234)
HUD_SHADE = (12, 12, 14)
HUD_SHADE_ALPHA = 150


def _to_surface(image: "carla.Image", pygame) -> "pygame.Surface":
    """
    Convert one CARLA camera frame into a pygame surface.

    :param image: The sensor frame, in CARLA's BGRA byte order.
    :param pygame: The imported pygame module.
    :return: A surface ready to blit.
    """
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = array.reshape((image.height, image.width, 4))[:, :, :3]
    # BGRA to RGB, then to pygame's column-major layout.
    return pygame.surfarray.make_surface(array[:, :, ::-1].swapaxes(0, 1))


class CarlaViewer:
    """
    A chase-camera window over a CARLA actor, with a 2D HUD.

    :param world: The connected CARLA world.
    :param follow: Actor the camera rides behind. The camera is attached, so
        it needs no per-tick repositioning and cannot drift.
    :param size: Window size in pixels.
    :param fov: Horizontal field of view, in degrees.
    :param title: Window caption.
    :raises RuntimeError: If pygame is not installed.
    """

    def __init__(
        self,
        world: "carla.World",
        follow: "carla.Actor",
        size: tuple[int, int] = (1280, 720),
        fov: float = 90.0,
        title: str = "autobus — CARLA",
    ) -> None:
        try:
            import pygame
        except ImportError as error:            # pragma: no cover
            raise RuntimeError(
                "the viewer needs pygame: conda install -c conda-forge "
                "pygame, or drop viewer=True"
            ) from error

        self._pygame = pygame
        self._world = world
        self._size = size
        self._latest: carla.Image | None = None
        self._closed = False
        self._last_drawn_ms = 0

        pygame.init()
        pygame.font.init()
        self._screen = pygame.display.set_mode(size)
        pygame.display.set_caption(title)
        self._font = pygame.font.SysFont("dejavusansmono", HUD_FONT_PT)
        self._clock = pygame.time.Clock()

        blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
        blueprint.set_attribute("image_size_x", str(size[0]))
        blueprint.set_attribute("image_size_y", str(size[1]))
        blueprint.set_attribute("fov", str(fov))
        blueprint.set_attribute("sensor_tick", str(SENSOR_TICK_S))
        self._camera = world.spawn_actor(
            blueprint,
            carla.Transform(
                carla.Location(x=-CAMERA_BEHIND_M, z=CAMERA_ABOVE_M),
                carla.Rotation(pitch=CAMERA_PITCH_DEG)),
            attach_to=follow,
        )
        # Only ever keeps the newest frame: the simulation outruns the
        # display, and showing a backlog would show the past.
        self._camera.listen(self._receive)

    @property
    def closed(self) -> bool:
        """Whether the window has been closed."""
        return self._closed

    def _receive(self, image: "carla.Image") -> None:
        """Keep the newest frame, dropping whatever it replaces."""
        self._latest = image

    def update(self, lines: Sequence[str]) -> bool:
        """
        Pump the window once and draw the HUD over the latest frame.

        Safe to call every tick: redraws are capped by wall clock, so the
        cost of calling it more often than the eye can use is one clock read.
        Never blocks on a frame — if the camera has not produced one yet, the
        window simply keeps what it had.
        :param lines: HUD text, one entry per line, drawn top-left.
        :return: False once the window has been closed, True otherwise.
        """
        if self._closed:
            return False

        for event in self._pygame.event.get():
            if event.type == self._pygame.QUIT:
                self._closed = True
                return False
            if (event.type == self._pygame.KEYDOWN
                    and event.key == self._pygame.K_ESCAPE):
                self._closed = True
                return False

        now_ms = self._pygame.time.get_ticks()
        if now_ms - self._last_drawn_ms < 1000 // MAX_FPS:
            return True
        self._last_drawn_ms = now_ms

        if self._latest is not None:
            self._screen.blit(_to_surface(self._latest, self._pygame), (0, 0))
        self._draw_hud(lines)
        self._pygame.display.flip()
        self._clock.tick()
        return True

    def _draw_hud(self, lines: Sequence[str]) -> None:
        """
        Draw the HUD text over a panel, top-left.

        The panel is what makes the text readable: CARLA's skies are bright
        and its shadows are dark, and plain text is illegible over one or the
        other whatever colour it is given.
        :param lines: One entry per line.
        :return: None.
        """
        if not lines:
            return
        rendered = [self._font.render(text, True, HUD_INK) for text in lines]
        width = max(surface.get_width() for surface in rendered)
        height = HUD_LINE_H * len(rendered)

        panel = self._pygame.Surface((width + HUD_PAD * 2,
                                      height + HUD_PAD * 2))
        panel.set_alpha(HUD_SHADE_ALPHA)
        panel.fill(HUD_SHADE)
        self._screen.blit(panel, (HUD_MARGIN - HUD_PAD,
                                  HUD_MARGIN - HUD_PAD))

        for index, surface in enumerate(rendered):
            self._screen.blit(surface,
                              (HUD_MARGIN, HUD_MARGIN + index * HUD_LINE_H))

    def close(self) -> None:
        """
        Take the camera down and shut the window.

        Safe to call twice, and safe to call on a window the user already
        closed, because a trial's teardown should not have to know either.
        :return: None.
        """
        if self._camera is not None:
            try:
                self._camera.stop()
                self._camera.destroy()
            except RuntimeError:
                # The actor may already be gone with the world; nothing here
                # is worth failing a trial's teardown over.
                pass
            self._camera = None
        if not self._closed:
            self._closed = True
        self._pygame.quit()
