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

import atexit
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import carla

# pygame's conda-forge build warns on import that it was compiled without
# AVX2 blit support. Measured before silencing it: converting and blitting a
# 1280x720 frame costs 0.51 ms against a 33 ms budget at 30 fps, so the blit
# is nowhere near this window's cost -- the camera sensor is. The warning
# names a compile-time flag nothing here can act on, so it is only noise in a
# notebook. Narrow on purpose: every other warning still gets through.
warnings.filterwarnings(
    "ignore", message=".*avx2.*", category=RuntimeWarning)

# SDL gives a process one window: calling ``set_mode`` again resizes the one
# that exists rather than opening a second. Two viewers at once would
# therefore draw into the same surface and fight over it, which looks like a
# flickering window rather than an error. Held so that can be refused.
_ACTIVE: "CarlaViewer | None" = None


def close_active() -> None:
    """
    Shut whatever viewer is open, if any.

    Registered with ``atexit`` for the reason ``carla_stdout.restore`` is: a
    run that dies part-way must not leave a window behind. Closing one
    normally deregisters nothing -- this simply finds nothing to do.
    :return: None.
    """
    if _ACTIVE is not None:
        _ACTIVE.close()


atexit.register(close_active)

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

# --- Minimap --------------------------------------------------------------
#
# The graph the policy plans over, not a picture of the town: it shows the
# roads the bus is allowed to use, which is where the detours the campaign
# keeps finding actually live. Drawn bottom-right.
MAP_SIZE = (360, 360)
MAP_MARGIN = 18
MAP_PAD = 14
MAP_FONT_PT = 15
MAP_SHADE = (10, 12, 16)
MAP_SHADE_ALPHA = 170
MAP_ROAD = (104, 112, 128)
MAP_ROAD_W = 1
# The road the bus is on, picked by nearest segment. This is the "edge" in
# the graph sense: TransitGraph's nodes are road edges.
MAP_HERE = (255, 246, 170)
MAP_HERE_W = 5
MAP_STOP = (150, 158, 172)
MAP_STOP_R = 3
MAP_STOP_NEXT = (235, 160, 40)
MAP_STOP_PLANNED = (60, 150, 235)
MAP_PLAN_W = 2
MAP_BUS = (70, 230, 150)
MAP_BUS_R = 5
MAP_LABEL_INK = (206, 212, 222)
# Labelling fifteen stops fills the panel with text. Only the plan's stops
# and whatever is near the bus get named.
MAP_LABEL_RANGE_M = 90.0

# --- Live-request table ---------------------------------------------------
#
# Drawn top-right, under its own panel. Columns are padded to a common width
# and rendered as plain lines, which only works because the font is
# monospaced -- with a proportional font this would need per-column blits.
TABLE_MARGIN = 18
TABLE_PAD = 12
TABLE_FONT_PT = 18
TABLE_LINE_H = 21
TABLE_GAP = "  "
TABLE_INK = (222, 226, 232)
TABLE_HEAD_INK = (150, 200, 245)
TABLE_SHADE = (10, 12, 16)
TABLE_SHADE_ALPHA = 170


def _to_surface(image: "carla.Image", pygame) -> "pygame.Surface":
    """
    Convert one CARLA camera frame into a pygame surface.

    ``frombuffer`` reads CARLA's bytes as they already are. Going through
    numpy to swap channels and axes measured 1.09 ms a frame against 0.51,
    and this runs on every frame the window draws.
    :param image: The sensor frame, in CARLA's BGRA byte order.
    :param pygame: The imported pygame module.
    :return: A surface ready to blit.
    """
    return pygame.image.frombuffer(
        image.raw_data, (image.width, image.height), "BGRA")


@dataclass(frozen=True)
class MiniMap:
    """
    The road graph, flattened to plain numbers for drawing.

    Deliberately not a ``TransitGraph``: the simulation layer is kept free of
    map-layer imports, so the map is handed over as coordinates the way stop
    positions already are.

    :param edges: Road segments as ``(x1, y1, x2, y2)`` in world metres.
    :param stops: Bus stops as ``(stop_id, x, y)``.
    """

    edges: tuple[tuple[float, float, float, float], ...]
    stops: tuple[tuple[str, float, float], ...]


def format_table(
    header: Sequence[str], rows: Sequence[Sequence[str]]
) -> list[str]:
    """
    Pad a table into monospaced lines, one per row.

    Column widths come from the widest cell in each column, header included,
    so a long stop name widens its column instead of pushing the row out of
    alignment.
    :param header: Column titles.
    :param rows: Row cells, each the same length as the header.
    :return: The header line followed by one line per row; empty for no
        header.
    """
    if not header:
        return []
    widths = [len(title) for title in header]
    for row in rows:
        for index, cell in enumerate(row[:len(widths)]):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        """Pad one row's cells to the column widths."""
        padded = [str(cell).ljust(widths[index])
                  for index, cell in enumerate(cells[:len(widths)])]
        return TABLE_GAP.join(padded).rstrip()

    return [line(header)] + [line(row) for row in rows]


def minimap_from(provider: object) -> MiniMap:
    """
    Build the minimap's geometry from any map provider.

    Duck-typed for the same reason ``stop_positions_from`` is: this module
    should not know which map layer it is being handed.
    :param provider: Any MapProvider exposing road nodes, edges and stops.
    :return: The flattened graph.
    """
    nodes = {node.id: (node.x, node.y) for node in provider.get_road_nodes()}
    edges = tuple(
        (nodes[edge.from_node_id][0], nodes[edge.from_node_id][1],
         nodes[edge.to_node_id][0], nodes[edge.to_node_id][1])
        for edge in provider.get_road_edges()
        if edge.from_node_id in nodes and edge.to_node_id in nodes
    )
    stops = tuple(
        (stop.edge.id, stop.x, stop.y) for stop in provider.get_stops()
    )
    return MiniMap(edges=edges, stops=stops)


class CarlaViewer:
    """
    A chase-camera window over a CARLA actor, with a 2D HUD.

    :param world: The connected CARLA world.
    :param follow: Actor the camera rides behind. The camera is attached, so
        it needs no per-tick repositioning and cannot drift.
    :param size: Window size in pixels.
    :param fov: Horizontal field of view, in degrees.
    :param title: Window caption.
    :param minimap: Road graph to draw bottom-right. None draws no panel.
    :raises RuntimeError: If pygame is not installed.
    """

    def __init__(
        self,
        world: "carla.World",
        follow: "carla.Actor",
        size: tuple[int, int] = (1280, 720),
        fov: float = 90.0,
        title: str = "autobus — CARLA",
        minimap: MiniMap | None = None,
    ) -> None:
        try:
            import pygame
        except ImportError as error:            # pragma: no cover
            raise RuntimeError(
                "the viewer needs pygame: conda install -c conda-forge "
                "pygame, or drop viewer=True"
            ) from error

        global _ACTIVE
        if _ACTIVE is not None and not _ACTIVE.closed:
            raise RuntimeError(
                "a viewer window is already open, and SDL gives a process "
                "only one: close the first, or run the second trial in its "
                "own process"
            )

        self._pygame = pygame
        self._world = world
        self._size = size
        self._latest: carla.Image | None = None
        self._closed = False
        self._last_drawn_ms = 0

        pygame.init()
        pygame.font.init()
        self._screen = pygame.display.set_mode(size)
        # What SDL actually gave, which is not always what was asked for: a
        # window manager may cap it, and a scaled desktop may hand back a
        # smaller surface. Laying panels out against the requested size puts
        # them off the right and bottom edges, by more the larger the ask.
        self._size = self._screen.get_size()
        pygame.display.set_caption(title)
        self._font = pygame.font.SysFont("dejavusansmono", HUD_FONT_PT)
        self._map_font = pygame.font.SysFont("dejavusansmono", MAP_FONT_PT)
        self._table_font = pygame.font.SysFont("dejavusansmono",
                                               TABLE_FONT_PT)
        self._clock = pygame.time.Clock()
        self._minimap = minimap
        self._map_rect = self._fit_minimap(minimap, self._size)

        blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
        # Sized to the surface rather than the request, so the frame fills
        # the window exactly instead of being cropped or leaving a border.
        blueprint.set_attribute("image_size_x", str(self._size[0]))
        blueprint.set_attribute("image_size_y", str(self._size[1]))
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
        _ACTIVE = self

    @property
    def closed(self) -> bool:
        """Whether the window has been closed."""
        return self._closed

    def _receive(self, image: "carla.Image") -> None:
        """Keep the newest frame, dropping whatever it replaces."""
        self._latest = image

    def _relayout(self) -> None:
        """
        Re-place the panels if the window is no longer the size it was.

        Cheap enough to check every frame — one call and a tuple compare —
        and it covers the cases a layout fixed at construction cannot: a
        window the manager resized, and a surface that only settles to its
        real size after the first frame.
        :return: None.
        """
        actual = self._screen.get_size()
        if actual == self._size:
            return
        self._size = actual
        self._map_rect = self._fit_minimap(self._minimap, actual)

    @staticmethod
    def _fit_minimap(
        minimap: "MiniMap | None", size: tuple[int, int]
    ) -> tuple[float, float, float, float] | None:
        """
        Work out how world metres map onto the minimap panel.

        Computed once: the graph does not move, so rescaling it every frame
        would be the same arithmetic forever. One scale for both axes, so the
        town keeps its shape rather than being stretched to fill a square.
        :param minimap: The graph, or None.
        :param size: Window size in pixels.
        :return: ``(origin_x, origin_y, metres_per_pixel, ...)`` as
            ``(off_x, off_y, scale, top)``, or None when there is no graph.
        """
        if minimap is None or not minimap.edges:
            return None
        xs = [v for edge in minimap.edges for v in (edge[0], edge[2])]
        ys = [v for edge in minimap.edges for v in (edge[1], edge[3])]
        span_x = max(xs) - min(xs) or 1.0
        span_y = max(ys) - min(ys) or 1.0
        inner = MAP_SIZE[0] - MAP_PAD * 2
        scale = min(inner / span_x, inner / span_y)
        # Centre the graph in the panel, then remember where the panel is.
        off_x = (min(xs) + max(xs)) / 2
        off_y = (min(ys) + max(ys)) / 2
        left = size[0] - MAP_SIZE[0] - MAP_MARGIN
        top = size[1] - MAP_SIZE[1] - MAP_MARGIN
        return (off_x, off_y, scale, (left, top))

    def _to_panel(self, x: float, y: float) -> tuple[int, int]:
        """
        Project a world point into panel pixels.

        :param x: World x, in metres.
        :param y: World y, in metres.
        :return: Pixel coordinates inside the window.
        """
        off_x, off_y, scale, (left, top) = self._map_rect
        return (int(left + MAP_SIZE[0] / 2 + (x - off_x) * scale),
                int(top + MAP_SIZE[1] / 2 + (y - off_y) * scale))

    def update(
        self,
        lines: Sequence[str],
        route: Sequence[str] = (),
        bus: "carla.Location | None" = None,
        table: tuple[Sequence[str], Sequence[Sequence[str]]] | None = None,
        hidden: int = 0,
    ) -> bool:
        """
        Pump the window once and draw the HUD over the latest frame.

        Safe to call every tick: redraws are capped by wall clock, so the
        cost of calling it more often than the eye can use is one clock read.
        Never blocks on a frame — if the camera has not produced one yet, the
        window simply keeps what it had.
        :param lines: HUD text, one entry per line, drawn top-left.
        :param route: Stops the bus plans to visit, next first, for the
            minimap to pick out.
        :param bus: Where the bus is, for the minimap to mark. None leaves
            the marker off rather than the panel.
        :param table: ``(header, rows)`` for the live-request table, drawn
            top-right. None draws no panel.
        :param hidden: How many rows were trimmed before being handed over,
            reported under the table so a backlog is not silently cropped.
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

        self._relayout()

        now_ms = self._pygame.time.get_ticks()
        if now_ms - self._last_drawn_ms < 1000 // MAX_FPS:
            return True
        self._last_drawn_ms = now_ms

        if self._latest is not None:
            self._screen.blit(_to_surface(self._latest, self._pygame), (0, 0))
        self._draw_hud(lines)
        self._draw_table(table, hidden)
        self._draw_minimap(route, bus)
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

    def _draw_table(
        self,
        table: "tuple[Sequence[str], Sequence[Sequence[str]]] | None",
        hidden: int,
    ) -> None:
        """
        Draw the live-request table, top-right, under its own panel.

        Nothing is drawn for an empty table rather than an empty box: early
        in a run there are no requests, and a permanent empty frame is just
        something else to look past.
        :param table: ``(header, rows)``, or None.
        :param hidden: Rows trimmed before this was handed over.
        :return: None.
        """
        if table is None:
            return
        header, rows = table
        if not rows:
            return

        text = format_table(header, rows)
        if hidden:
            text.append(f"... {hidden} more")
        rendered = [
            self._table_font.render(
                line, True,
                TABLE_HEAD_INK if index == 0 else TABLE_INK)
            for index, line in enumerate(text)
        ]
        width = max(surface.get_width() for surface in rendered)
        height = TABLE_LINE_H * len(rendered)
        # Clamped, so a table too wide for the window loses its right edge
        # rather than sliding off the left where the counters are.
        left = max(TABLE_MARGIN + TABLE_PAD,
                   self._size[0] - width - TABLE_MARGIN - TABLE_PAD)

        panel = self._pygame.Surface((width + TABLE_PAD * 2,
                                      height + TABLE_PAD * 2))
        panel.set_alpha(TABLE_SHADE_ALPHA)
        panel.fill(TABLE_SHADE)
        self._screen.blit(panel, (left - TABLE_PAD, TABLE_MARGIN - TABLE_PAD))

        for index, surface in enumerate(rendered):
            self._screen.blit(
                surface, (left, TABLE_MARGIN + index * TABLE_LINE_H))

    def _nearest_edge(
        self, bus: "carla.Location"
    ) -> tuple[float, float, float, float] | None:
        """
        Find the road segment the bus is on.

        ``TransitGraph``'s nodes *are* road edges, so "which edge is it on" is
        the graph question, and the honest answer from a world position is the
        nearest segment. Distance to the segment, not to its endpoints: on a
        long road the endpoints can both be far away while the bus sits on it.
        :param bus: Where the bus is, in world metres.
        :return: The closest segment, or None if the graph is empty.
        """
        best, best_d2 = None, float("inf")
        for x1, y1, x2, y2 in self._minimap.edges:
            dx, dy = x2 - x1, y2 - y1
            span = dx * dx + dy * dy
            if span == 0.0:
                along = 0.0
            else:
                along = ((bus.x - x1) * dx + (bus.y - y1) * dy) / span
                along = min(1.0, max(0.0, along))
            gap_x = bus.x - (x1 + along * dx)
            gap_y = bus.y - (y1 + along * dy)
            gap = gap_x * gap_x + gap_y * gap_y
            if gap < best_d2:
                best, best_d2 = (x1, y1, x2, y2), gap
        return best

    def _draw_minimap(
        self, route: Sequence[str], bus: "carla.Location | None"
    ) -> None:
        """
        Draw the road graph, the plan on it, and where the bus is.

        This is the graph the policy plans over rather than a picture of the
        town, which is the point: a detour that looks inexplicable out of the
        windscreen is usually plain here, because the road the bus wanted was
        never an edge.
        :param route: Stops still to visit, next first.
        :param bus: Where the bus is, or None to leave the marker off.
        :return: None.
        """
        if self._map_rect is None:
            return
        _, _, _, (left, top) = self._map_rect
        pygame = self._pygame

        panel = pygame.Surface(MAP_SIZE)
        panel.set_alpha(MAP_SHADE_ALPHA)
        panel.fill(MAP_SHADE)
        self._screen.blit(panel, (left, top))

        for x1, y1, x2, y2 in self._minimap.edges:
            pygame.draw.line(self._screen, MAP_ROAD, self._to_panel(x1, y1),
                             self._to_panel(x2, y2), MAP_ROAD_W)

        # The plan, as a chain through the stops it visits in order. Straight
        # hops, not driven paths: this says what the bus intends, and the
        # gap between that and the green line in the world is the detour.
        placed = {stop_id: (x, y) for stop_id, x, y in self._minimap.stops}
        chain = [placed[s] for s in route if s in placed]
        if bus is not None and chain:
            chain.insert(0, (bus.x, bus.y))
        for start, end in zip(chain, chain[1:]):
            pygame.draw.line(self._screen, MAP_STOP_PLANNED,
                             self._to_panel(*start), self._to_panel(*end),
                             MAP_PLAN_W)

        # After the plan and before the stops: this is where the bus is, so
        # nothing else gets to cover it.
        if bus is not None:
            here = self._nearest_edge(bus)
            if here is not None:
                pygame.draw.line(self._screen, MAP_HERE,
                                 self._to_panel(here[0], here[1]),
                                 self._to_panel(here[2], here[3]), MAP_HERE_W)

        planned = set(route)
        next_stop = route[0] if route else None
        for stop_id, x, y in self._minimap.stops:
            if stop_id == next_stop:
                color, radius = MAP_STOP_NEXT, MAP_STOP_R + 2
            elif stop_id in planned:
                color, radius = MAP_STOP_PLANNED, MAP_STOP_R + 1
            else:
                color, radius = MAP_STOP, MAP_STOP_R
            at = self._to_panel(x, y)
            pygame.draw.circle(self._screen, color, at, radius)

            # Naming all fifteen fills the panel with overlapping text, so
            # name the ones in play and whatever the bus is passing.
            near = (bus is not None
                    and math.hypot(bus.x - x, bus.y - y) <= MAP_LABEL_RANGE_M)
            if stop_id in planned or near:
                label = self._map_font.render(stop_id, True, MAP_LABEL_INK)
                self._screen.blit(
                    label, self._label_at(at, label.get_width(), radius))

        if bus is not None:
            pygame.draw.circle(self._screen, MAP_BUS,
                               self._to_panel(bus.x, bus.y), MAP_BUS_R)

    def _label_at(
        self, anchor: tuple[int, int], width: int, radius: int
    ) -> tuple[int, int]:
        """
        Place a stop's name beside it without running off the panel.

        To the right by default, and flipped to the left when that would
        overflow — a name clipped by the panel edge is worse than no name,
        because a truncated stop id reads as a different stop.
        :param anchor: Where the stop's dot is, in window pixels.
        :param width: Rendered width of the name.
        :param radius: Radius of the dot, so the text clears it.
        :return: Top-left corner to blit the name at.
        """
        _, _, _, (left, top) = self._map_rect
        gap = radius + 3
        x = anchor[0] + gap
        if x + width > left + MAP_SIZE[0] - MAP_PAD // 2:
            x = anchor[0] - gap - width
        x = max(left + 2, x)
        y = min(max(anchor[1] - 7, top + 2), top + MAP_SIZE[1] - 16)
        return x, y

    def close(self) -> None:
        """
        Take the camera down and shut the window.

        Safe to call twice, and safe to call on a window the user already
        closed, because a trial's teardown should not have to know either.
        :return: None.
        """
        global _ACTIVE
        if self._camera is not None:
            try:
                self._camera.stop()
                self._camera.destroy()
            except RuntimeError:
                # The actor may already be gone with the world; nothing here
                # is worth failing a trial's teardown over.
                pass
            self._camera = None
        self._closed = True
        if _ACTIVE is self:
            _ACTIVE = None
        # Only the display: ``pygame.quit()`` also tears down font and every
        # other module, which is not this object's to take away -- a later
        # trial in the same process, or anything else using pygame, would
        # find them gone.
        self._pygame.display.quit()
