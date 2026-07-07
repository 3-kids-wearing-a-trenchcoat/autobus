"""animate_trial: build a matplotlib animation from recorded bus-run frames."""

import math

import matplotlib.animation as mpl_animation
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from IPython import get_ipython
from matplotlib.collections import LineCollection

from src.experiment.trial import TrialResult
from src.map.TransitGraph import TransitGraph
from src.simulation.AnimationRecorder import AnimationFrame

# Keeps FuncAnimation instances alive: nothing else holds a reference once
# show_animation returns, and matplotlib silently stops/GCs unreferenced
# animations.
_live_animations: list[mpl_animation.FuncAnimation] = []


def _build_road_segments(
    transit_graph: TransitGraph,
    node_xy: dict[str, tuple[float, float]],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """
    Build a list of (start_xy, end_xy) pairs for every road edge.

    :param transit_graph: The TransitGraph used during the run.
    :param node_xy: Mapping from road node ID to (x, y) coordinates.
    :return: List of segment endpoint pairs suitable for LineCollection.
    """
    segments = []
    for _, data in transit_graph.map.nodes(data=True):
        from_xy = node_xy.get(str(data["start_node"]))
        to_xy = node_xy.get(str(data["stop_node"]))
        if from_xy is not None and to_xy is not None:
            segments.append((from_xy, to_xy))
    return segments


def _max_pending(frames: list[AnimationFrame]) -> int:
    """
    Return the highest waiting-passenger count seen across all frames.

    :param frames: List of AnimationFrames to inspect.
    :return: Maximum per-stop pending count, at least 1.
    """
    peak = max(
        (max(f.pending_counts.values(), default=0) for f in frames),
        default=0,
    )
    return max(peak, 1)


def animate_trial(
    frames: list[AnimationFrame],
    transit_graph: TransitGraph,
    interval_ms: int = 300,
    figsize: tuple[float, float] = (10.0, 8.0),
) -> mpl_animation.FuncAnimation:
    """
    Build a matplotlib FuncAnimation from a recorded bus run.

    Each frame corresponds to one stop arrival.  The animation shows:
    - Road network as gray lines.
    - Bus stops as circles whose size and colour encode the waiting
      passenger count (small/white = few, large/steelblue = many).
    - A numeric label above each stop showing its exact waiting count
      (hidden when count is 0).
    - Destination stops for onboard passengers highlighted with an
      orange ring.
    - The bus as a black marker, with an arrow indicating heading.
    - A HUD showing onboard count and elapsed simulation time.

    Display in Jupyter with::

        from IPython.display import HTML
        HTML(animate_trial(frames, tg).to_jshtml())

    :param frames: Per-stop-arrival snapshots, e.g. from
        TrialResult.animation_frames.
    :param transit_graph: The TransitGraph used during the run (used to
        look up road node and stop coordinates).
    :param interval_ms: Milliseconds between frames in the animation.
    :param figsize: (width, height) in inches for the figure.
    :return: matplotlib FuncAnimation object.
    """
    if not frames:
        raise ValueError("frames must be non-empty")

    # ------------------------------------------------------------------
    # Pre-compute coordinate lookups
    # ------------------------------------------------------------------
    road_nodes = transit_graph.provider.get_road_nodes()
    node_xy: dict[str, tuple[float, float]] = {
        n.id: (n.x, n.y) for n in road_nodes
    }

    all_stops = transit_graph.get_stops()
    stop_ids = [s.id for s in all_stops]
    stop_xy: dict[str, tuple[float, float]] = {
        s.id: (s.x, s.y) for s in all_stops
    }

    road_segments = _build_road_segments(transit_graph, node_xy)
    max_wait = _max_pending(frames)

    # ------------------------------------------------------------------
    # Figure and axes
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect("equal")
    ax.axis("off")

    # Axis bounds from all road node positions
    if node_xy:
        xs = [xy[0] for xy in node_xy.values()]
        ys = [xy[1] for xy in node_xy.values()]
        margin_x = (max(xs) - min(xs)) * 0.05 or 1.0
        margin_y = (max(ys) - min(ys)) * 0.05 or 1.0
        ax.set_xlim(min(xs) - margin_x, max(xs) + margin_x)
        ax.set_ylim(min(ys) - margin_y, max(ys) + margin_y)

    # ------------------------------------------------------------------
    # Static artists (background layer, drawn once)
    # ------------------------------------------------------------------
    if road_segments:
        lc = LineCollection(
            road_segments, colors="lightgray", linewidths=0.8, zorder=1
        )
        ax.add_collection(lc)

    # Stop name labels (fixed, not animated)
    for s in all_stops:
        label = s.name if s.name else s.id[-4:]
        sx, sy = stop_xy[s.id]
        ax.text(
            sx, sy - 3.5, label,
            fontsize=5, ha="center", va="top",
            color="dimgray", zorder=2,
        )

    # ------------------------------------------------------------------
    # Animated artists (created once, mutated in _update)
    # ------------------------------------------------------------------
    init_xs = [stop_xy[sid][0] for sid in stop_ids]
    init_ys = [stop_xy[sid][1] for sid in stop_ids]

    stop_scatter = ax.scatter(
        init_xs, init_ys,
        s=40, c="white", edgecolors="steelblue",
        linewidths=1.2, zorder=3,
    )

    dest_scatter = ax.scatter(
        [], [],
        s=220, facecolors="none", edgecolors="tomato",
        linewidths=2.0, zorder=4,
    )

    # One count label per stop, positioned above its circle
    count_labels = [
        ax.text(
            stop_xy[sid][0], stop_xy[sid][1] + 3.5, "",
            fontsize=6, ha="center", va="bottom",
            color="steelblue", zorder=5,
        )
        for sid in stop_ids
    ]

    bus_dot, = ax.plot([], [], "o", color="black", markersize=9, zorder=6)

    # Heading arrow: replaced each frame via remove/re-add
    arrow_container: list[mpatches.FancyArrow] = []

    hud = ax.text(
        0.02, 0.97, "",
        transform=ax.transAxes,
        fontsize=9, va="top", fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.7),
        zorder=7,
    )

    # ------------------------------------------------------------------
    # Per-frame update function
    # ------------------------------------------------------------------
    def _update(frame_idx: int) -> list:
        f: AnimationFrame = frames[frame_idx]

        # Stop circles: size and colour by waiting count
        sizes = []
        face_colors = []
        for sid in stop_ids:
            count = f.pending_counts.get(sid, 0)
            sizes.append(40 + 200 * count / max_wait)
            # Interpolate from white (0) to steelblue (#4682B4) by fraction
            frac = count / max_wait
            r = 1.0 - frac * (1.0 - 0x46 / 255)
            g = 1.0 - frac * (1.0 - 0x82 / 255)
            b = 1.0 - frac * (1.0 - 0xB4 / 255)
            face_colors.append((r, g, b))
        stop_scatter.set_sizes(sizes)
        stop_scatter.set_facecolors(face_colors)

        # Count labels: show non-zero counts, hide zeros
        for label_artist, sid in zip(count_labels, stop_ids):
            count = f.pending_counts.get(sid, 0)
            label_artist.set_text(str(count) if count > 0 else "")

        # Destination rings
        dest_coords = [
            stop_xy[sid]
            for sid in f.dropoff_stop_ids
            if sid in stop_xy
        ]
        if dest_coords:
            dest_scatter.set_offsets(np.array(dest_coords))
        else:
            dest_scatter.set_offsets(np.empty((0, 2)))

        # Bus position
        bx, by = stop_xy.get(f.bus_stop_id, (0.0, 0.0))
        bus_dot.set_data([bx], [by])

        # Heading arrow: remove old, add new
        if arrow_container:
            arrow_container[0].remove()
            arrow_container.clear()
        if (
            f.prev_stop_id is not None
            and f.prev_stop_id in stop_xy
        ):
            px, py = stop_xy[f.prev_stop_id]
            dx, dy = bx - px, by - py
            length = math.hypot(dx, dy)
            if length > 0:
                # Cap arrow length at 15 % of the segment, min 2 units
                scale = max(min(length * 0.15, 20.0), 2.0)
                arrow = mpatches.FancyArrow(
                    bx, by,
                    dx / length * scale, dy / length * scale,
                    width=1.5, head_width=4.0, head_length=3.0,
                    color="black", length_includes_head=True,
                    zorder=6,
                )
                ax.add_patch(arrow)
                arrow_container.append(arrow)

        # HUD
        elapsed_min = f.sim_time / 60.0
        hud.set_text(
            f"On bus: {f.onboard_count}   "
            f"Time: {elapsed_min:.1f} min"
        )

        artists: list = [stop_scatter, dest_scatter, bus_dot, hud]
        artists.extend(count_labels)
        artists.extend(arrow_container)
        return artists

    anim = mpl_animation.FuncAnimation(
        fig,
        _update,
        frames=len(frames),
        interval=interval_ms,
        blit=False,
    )
    return anim


def show_animation(
    result: TrialResult,
    interval_ms: int = 300,
    figsize: tuple[float, float] = (10.0, 8.0),
) -> None:
    """
    Build and display a trial's animation in one call, for notebook use.

    Switches to the ipympl (``%matplotlib widget``) backend if running
    under IPython so playback is interactive, then builds and shows the
    animation via animate_trial. Unlike the plot_* helpers, this
    displays directly rather than returning a Figure, since it is meant
    to be the last statement in a notebook cell.

    :param result: TrialResult produced by run_trial(...,
        record_animation=True); must have non-empty animation_frames
        and a non-None transit_graph.
    :param interval_ms: Milliseconds between frames in the animation.
    :param figsize: (width, height) in inches for the figure.
    :return: None.
    """
    if not result.animation_frames or result.transit_graph is None:
        raise ValueError(
            "result has no animation data; call run_trial(..., "
            "record_animation=True) to capture animation_frames and "
            "transit_graph before calling show_animation"
        )

    try:
        get_ipython().run_line_magic("matplotlib", "widget")
    except Exception:
        pass

    anim = animate_trial(
        result.animation_frames,
        result.transit_graph,
        interval_ms=interval_ms,
        figsize=figsize,
    )
    _live_animations.append(anim)
    plt.show()
