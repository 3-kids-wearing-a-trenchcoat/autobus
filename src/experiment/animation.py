"""Build and display matplotlib animations from recorded bus-run frames."""

import math
from typing import Callable

import matplotlib.animation as mpl_animation
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.widgets as mpl_widgets
import numpy as np
from IPython import get_ipython
from matplotlib.collections import LineCollection

from src.experiment.trial import TrialResult
from src.map.TransitGraph import TransitGraph
from src.simulation.AnimationRecorder import AnimationFrame

# Keeps FuncAnimation instances alive: nothing else holds a reference once
# show_animation/show_animations returns, and matplotlib silently
# stops/GCs unreferenced animations.
_live_animations: list[mpl_animation.FuncAnimation] = []

# Same reason, for the matplotlib.widgets.Button instances added by
# _add_playback_controls: without a live reference their click callbacks
# stop firing.
_live_controls: list[list[mpl_widgets.Button]] = []


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


def _build_panel(
    ax: plt.Axes,
    transit_graph: TransitGraph,
    frames: list[AnimationFrame],
) -> Callable[[int], list]:
    """
    Set up one panel's static and animated artists on ax.

    :param ax: Axes to draw this panel's animation into.
    :param transit_graph: TransitGraph used during the recorded run.
    :param frames: Per-stop-arrival snapshots for this panel.
    :return: update(frame_idx) callable that mutates this panel's
        artists for the given frame and returns the animated artists.
        frame_idx is clamped to len(frames) - 1, so a panel with fewer
        frames than a shared multi-panel animation simply holds its
        last frame once its own trial has finished.
    """
    ax.set_aspect("equal")
    ax.axis("off")

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
    # Animated artists (created once, mutated in update)
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
    def update(frame_idx: int) -> list:
        f: AnimationFrame = frames[min(frame_idx, len(frames) - 1)]

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

    return update


def _validate_recorded(label: str, result: TrialResult) -> None:
    """
    Raise ValueError if result was not run with record_animation=True.

    :param label: Name used to identify result in the error message.
    :param result: TrialResult to check.
    :return: None.
    """
    if not result.animation_frames or result.transit_graph is None:
        raise ValueError(
            f"{label} has no animation data; call run_trial(..., "
            "record_animation=True) to capture animation_frames and "
            "transit_graph before animating it"
        )


def _switch_to_widget_backend() -> None:
    """Best-effort switch to the ipympl (widget) backend under IPython."""
    try:
        get_ipython().run_line_magic("matplotlib", "widget")
    except Exception:
        pass


def _add_playback_controls(
    fig: plt.Figure,
    anim: mpl_animation.FuncAnimation,
    base_interval_ms: int,
) -> None:
    """
    Add pause/resume and speed buttons to fig, wired to anim.

    :param fig: Figure to attach the control row to.
    :param anim: FuncAnimation being controlled.
    :param base_interval_ms: The 1x playback interval, used to compute
        the 0.5x/2x speed settings.
    :return: None.
    """
    fig.subplots_adjust(bottom=0.18)

    pause_ax = fig.add_axes([0.35, 0.02, 0.1, 0.06])
    pause_button = mpl_widgets.Button(pause_ax, "Pause")
    state = {"paused": False}

    def _toggle_pause(_event: object) -> None:
        if state["paused"]:
            anim.resume()
            pause_button.label.set_text("Pause")
        else:
            anim.pause()
            pause_button.label.set_text("Resume")
        state["paused"] = not state["paused"]

    pause_button.on_clicked(_toggle_pause)

    speed_buttons = [pause_button]
    for i, (label, speed) in enumerate(
        [("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)]
    ):
        speed_ax = fig.add_axes([0.5 + i * 0.12, 0.02, 0.1, 0.06])
        button = mpl_widgets.Button(speed_ax, label)
        button.on_clicked(
            lambda _event, s=speed: setattr(
                anim.event_source, "interval", base_interval_ms / s
            )
        )
        speed_buttons.append(button)

    _live_controls.append(speed_buttons)


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

    fig, ax = plt.subplots(figsize=figsize)
    update = _build_panel(ax, transit_graph, frames)

    return mpl_animation.FuncAnimation(
        fig,
        update,
        frames=len(frames),
        interval=interval_ms,
        blit=False,
    )


def show_animation(
    result: TrialResult,
    interval_ms: int = 300,
    figsize: tuple[float, float] = (10.0, 8.0),
) -> None:
    """
    Build and display a trial's animation in one call, for notebook use.

    Switches to the ipympl (``%matplotlib widget``) backend if running
    under IPython so playback is interactive, then displays the
    animation with pause/resume and speed (0.5x/1x/2x) buttons. Unlike
    the plot_* helpers, this displays directly rather than returning a
    Figure, since it is meant to be the last statement in a notebook
    cell.

    :param result: TrialResult produced by run_trial(...,
        record_animation=True); must have non-empty animation_frames
        and a non-None transit_graph.
    :param interval_ms: Milliseconds between frames in the animation.
    :param figsize: (width, height) in inches for the figure.
    :return: None.
    """
    _validate_recorded("result", result)
    _switch_to_widget_backend()

    fig, ax = plt.subplots(figsize=figsize)
    update = _build_panel(ax, result.transit_graph, result.animation_frames)
    anim = mpl_animation.FuncAnimation(
        fig,
        update,
        frames=len(result.animation_frames),
        interval=interval_ms,
        blit=False,
    )
    _live_animations.append(anim)
    _add_playback_controls(fig, anim, interval_ms)
    plt.show()


def show_animations(
    results: dict[str, TrialResult],
    interval_ms: int = 300,
    figsize: tuple[float, float] | None = None,
) -> None:
    """
    Display multiple trials' animations side by side, synchronized.

    All panels are driven by a single FuncAnimation/frame index, so
    they always show the same step at the same time and share one set
    of pause/resume and speed controls. A panel whose trial has fewer
    frames than the longest one freezes on its last frame once
    exhausted, rather than cutting the others off.

    :param results: Mapping from label to TrialResult, e.g. one entry
        per policy from compare_policies. Each value must have been
        produced with record_animation=True.
    :param interval_ms: Milliseconds between frames in the animation.
    :param figsize: (width, height) in inches; defaults to
        (5.0 * len(results), 5.0).
    :return: None.
    """
    if not results:
        raise ValueError("results must be non-empty")
    for label, result in results.items():
        _validate_recorded(f"results[{label!r}]", result)

    _switch_to_widget_backend()

    labels = list(results.keys())
    figsize = figsize or (5.0 * len(labels), 5.0)
    fig, axes = plt.subplots(1, len(labels), figsize=figsize)
    if len(labels) == 1:
        axes = [axes]

    updates = []
    max_frames = 0
    for ax, label in zip(axes, labels):
        result = results[label]
        ax.set_title(label)
        updates.append(
            _build_panel(ax, result.transit_graph, result.animation_frames)
        )
        max_frames = max(max_frames, len(result.animation_frames))

    def update_all(frame_idx: int) -> list:
        artists: list = []
        for update in updates:
            artists.extend(update(frame_idx))
        return artists

    anim = mpl_animation.FuncAnimation(
        fig,
        update_all,
        frames=max_frames,
        interval=interval_ms,
        blit=False,
    )
    _live_animations.append(anim)
    _add_playback_controls(fig, anim, interval_ms)
    plt.show()
