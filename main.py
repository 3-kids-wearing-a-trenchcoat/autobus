"""
Autobus demo: run one simulation, watch it, and report how it went.

This is the project's entry point for someone who wants to *see* the system
work rather than read a KPI table. It runs a single trial -- by default in
CARLA with a chase-camera window open -- and prints a summary when the bus
is done.

Every knob has a default defined below, and every default can be overridden
by a command-line flag. Flags that are not supplied keep their default, so
``./run.sh main.py`` on its own is a complete demo and
``./run.sh main.py --policy fixed`` changes only the routing policy.
Run ``./run.sh main.py --help`` for the full list.

**Nothing here is recorded.** The demo calls ``run_trial`` directly rather
than ``run_campaign``, so it writes nothing to ``results/runs`` and cannot
collide with a campaign's run identities. It is a demonstration, not a
measurement -- reproducible numbers come from the notebooks.
"""

import argparse
import os
import socket
import sys
import time
from typing import Callable

from tqdm.auto import tqdm

from src.algorithm.DispatchPolicy import DispatchPolicy
from src.algorithm.FixedRoutePolicy import FixedRoutePolicy
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.algorithm.ReorderingInsertionPolicy import ReorderingInsertionPolicy
from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.experiment.trial import (
    ALLOW_UNPINNED_HASH, TrialResult, run_trial,
)
from src.simulation.SimulationRunner import TickProgress
from src.map.CarlaMapProvider import CarlaMapProvider
from src.map.MapProvider import MapProvider
from src.map.PlanarMapProvider import PlanarMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.carla_stops import available_towns
from src.map.graph import PlanarStreetGraph

# ---------------------------------------------------------------------------
# Defaults
#
# These are the values used when the matching flag is not given. They are
# deliberately the campaign's own scenario (see
# notebooks/carla-campaign-towns.ipynb), so what the examiner watches is the
# same service that was measured -- not a prettier one invented for the demo.
# ---------------------------------------------------------------------------

# Where the bus drives.
DEFAULT_BACKEND = "carla"       # "carla" needs a live server; "mock" does not
DEFAULT_TOWN = "Town01"

# The service.
DEFAULT_POLICY = "reordering"
DEFAULT_CAPACITY = 20
DEFAULT_MAX_REQUESTS = 30       # passengers over the whole run
DEFAULT_DEMAND_INTERVAL_S = 90.0
DEFAULT_WINDOW_S = 750.0        # the policy's promise to a waiting passenger
DEFAULT_PATIENCE_S = 750.0      # when a waiting passenger gives up
DEFAULT_DWELL_S = 10.0          # how long the bus holds at each stop

# The run.
DEFAULT_SEED = 0
DEFAULT_N_STOPS = 20            # stops the policy plans ahead
DEFAULT_COMMITMENT = 0          # locked prefix; 0 is the measured optimum
DEFAULT_MAX_TIME_S = 40000.0    # safety ceiling in simulated seconds
TICK_S = 1.0                    # run_trial's step; one tick, one sim second

# Watching, and CARLA itself.
DEFAULT_NPC_VEHICLES = 20
DEFAULT_VIEWER_WIDTH = 1920
DEFAULT_VIEWER_HEIGHT = 1080

# Resolution the chase camera actually renders at, before its frame is
# scaled into the window. ``None`` follows the window exactly -- whatever
# --viewer-size says, including its defaults above -- which is the right
# answer unless the two should deliberately differ, and costs no scaling at
# all. Set them below the window to buy back tick rate on a large display,
# or above it for a sharper picture than the window alone would give.
DEFAULT_RENDER_WIDTH: int | None = None
DEFAULT_RENDER_HEIGHT: int | None = None
DEFAULT_FIXED_DELTA_S = 0.05    # never above 0.1: CARLA's substepping limit
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 2000

# How long to wait for the CARLA port to accept a connection before deciding
# nobody is there. This is a local TCP handshake against a server that is
# either listening or not, so it needs no patience -- and every second here
# is a second the demo looks hung for.
PREFLIGHT_TIMEOUT_S = 2.0

# Printed whenever the demo cannot reach a server. `docker compose up -d`
# rather than a `docker run` of the stock carlasim image: that image ships
# Mesa 21.2, which predates this project's GPUs and exits within 30 s. See
# compose.yml.
NO_SERVER_HELP = (
    "If the server is not running, start one with:\n"
    "    docker compose up -d\n"
    "and watch it come up with:\n"
    "    docker compose logs -f carla\n"
)

# The synthetic map used by --backend mock. These are the campus map the
# synthetic campaigns measured, so a mock demo is comparable to them.
MOCK_N_SEEDS = 17
MOCK_BOUNDS = (0.0, 0.0, 2000.0, 2000.0)
MOCK_STOP_DENSITY = 0.5

# ---------------------------------------------------------------------------
# The policy registry
#
# Kept here rather than in src/ because it is a demo's menu, not library
# code: it exists so the examiner can name a policy on the command line.
# Each entry takes the graph and the parsed arguments, so a policy can read
# whichever knobs it cares about.
# ---------------------------------------------------------------------------

PolicyFactory = Callable[[TransitGraph, argparse.Namespace], DispatchPolicy]

POLICIES: dict[str, PolicyFactory] = {
    # The section 3.2.2 baseline: a fixed cycle through every stop.
    "fixed": lambda graph, args: FixedRoutePolicy(graph),
    # Greedy demand response, with no promise about waiting time.
    "nearest": lambda graph, args: NearestUnservedPolicy(graph),
    # L0: insertion under a service window.
    "window": lambda graph, args: WindowInsertionPolicy(
        graph, max_wait_s=args.window
    ),
    # L1: the same, plus reordering of the stops already planned.
    "reordering": lambda graph, args: ReorderingInsertionPolicy(
        graph, max_wait_s=args.window
    ),
}

POLICY_HELP = {
    "fixed": "fixed cycle through every stop (the baseline)",
    "nearest": "greedily drive to the nearest stop with demand",
    "window": "insertion under a service window (L0)",
    "reordering": "insertion plus reordering (L1, the recommendation)",
}


def build_parser() -> argparse.ArgumentParser:
    """
    Describe every flag the demo accepts.

    Each flag's ``default`` is the matching constant above, which is what
    makes an unsupplied flag fall back rather than fail. ``-h/--help`` is
    added by argparse itself.
    :return: The parser, ready to parse sys.argv.
    """
    parser = argparse.ArgumentParser(
        prog="run.sh main.py",
        description=(
            "Run one Autobus simulation and report how it went. Every "
            "option has a default, so no arguments at all is a complete "
            "demo. Nothing is written to results/runs."
        ),
        epilog=(
            "example: run.sh main.py --town Town03 --policy fixed "
            "--max-requests 10"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    world = parser.add_argument_group("where the bus drives")
    world.add_argument(
        "--backend", choices=("carla", "mock"), default=DEFAULT_BACKEND,
        help="carla drives a real bus and needs a running server; mock "
             "simulates travel times on the graph and needs nothing",
    )
    world.add_argument(
        # Read from the authored stop files rather than listed here, so a
        # town added later needs no edit to this parser.
        "--town", choices=available_towns(), default=DEFAULT_TOWN,
        help="CARLA town to drive in; ignored by --backend mock",
    )

    service = parser.add_argument_group("the service")
    service.add_argument(
        "--policy", choices=sorted(POLICIES), default=DEFAULT_POLICY,
        help="how the bus decides where to go next",
    )
    service.add_argument(
        "--capacity", type=int, default=DEFAULT_CAPACITY,
        help="seats on the bus",
    )
    service.add_argument(
        "--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
        help="passengers created over the whole run",
    )
    service.add_argument(
        "--demand-interval", type=float, default=DEFAULT_DEMAND_INTERVAL_S,
        metavar="SECONDS", help="simulated seconds between new passengers",
    )
    service.add_argument(
        "--window", type=float, default=DEFAULT_WINDOW_S, metavar="SECONDS",
        help="the service window the policy promises; window and "
             "reordering only",
    )
    service.add_argument(
        "--patience", type=float, default=DEFAULT_PATIENCE_S,
        metavar="SECONDS",
        help="how long a passenger waits before giving up",
    )
    service.add_argument(
        "--dwell", type=float, default=DEFAULT_DWELL_S, metavar="SECONDS",
        help="how long the bus holds at each stop",
    )

    run = parser.add_argument_group("the run")
    run.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help="seed for demand and for CARLA traffic",
    )
    run.add_argument(
        "--n-stops", type=int, default=DEFAULT_N_STOPS,
        help="stops the policy plans ahead each time it re-plans",
    )
    run.add_argument(
        "--commitment", type=int, default=DEFAULT_COMMITMENT,
        help="leading stops locked in when re-planning; 0 measured best",
    )
    run.add_argument(
        "--max-time", type=float, default=DEFAULT_MAX_TIME_S,
        metavar="SECONDS",
        help="safety ceiling; the run ends here even if demand remains",
    )

    watching = parser.add_argument_group("watching, and CARLA")
    watching.add_argument(
        "--no-viewer", action="store_true",
        help="do not open the chase-camera window; the run goes faster "
             "and there is nothing to see",
    )
    watching.add_argument(
        "--no-markers", action="store_true",
        help="do not draw the plan into the world: the route the bus was "
             "told to drive, a labelled box over every stop, and a marker "
             "wherever that route was thrown away",
    )
    watching.add_argument(
        "--no-scenery", action="store_true",
        help="do not dress a generated town: no ground, no buildings and "
             "no posts at the stops. Shipped CARLA towns bring their own "
             "art and ignore this either way",
    )
    watching.add_argument(
        "--npc-vehicles", type=int, default=DEFAULT_NPC_VEHICLES,
        help="other traffic in the town; each one costs throughput",
    )
    watching.add_argument(
        "--viewer-size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
        default=[DEFAULT_VIEWER_WIDTH, DEFAULT_VIEWER_HEIGHT],
        help="size of the chase-camera window",
    )
    watching.add_argument(
        "--render-size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
        default=[DEFAULT_RENDER_WIDTH, DEFAULT_RENDER_HEIGHT],
        help="resolution the camera renders at before the frame is scaled "
             "into the window; defaults to matching --viewer-size exactly",
    )
    watching.add_argument(
        "--host", default=DEFAULT_HOST, help="CARLA server host",
    )
    watching.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="CARLA server port",
    )

    listing = parser.add_argument_group("just tell me what exists")
    listing.add_argument(
        "--list-towns", action="store_true",
        help="print the towns that have authored stops, and exit",
    )
    listing.add_argument(
        "--list-policies", action="store_true",
        help="print the routing policies, and exit",
    )

    return parser


def allow_unpinned_hash() -> bool:
    """
    Let the demo run even when it could not be reproduced exactly.

    ``run_trial`` refuses a seeded trial while PYTHONHASHSEED is unpinned,
    because several policies break ties with ``min()`` over a ``set[str]``
    and would build a different route in another process. That matters for
    a measurement and not for a demonstration, so this takes the escape
    hatch ``run_trial`` already publishes rather than making the examiner
    find the right launcher first. ``./run.sh`` still pins the seed, so a
    demo launched that way stays reproducible and prints no warning.
    :return: True if the warning applies, i.e. the hash seed is not pinned.
    """
    pinned = os.environ.get("PYTHONHASHSEED")
    if pinned is not None and pinned != "random":
        return False
    os.environ[ALLOW_UNPINNED_HASH] = "1"
    return True


def prefer_x11_on_wayland() -> None:
    """
    Give SDL a driver that can actually open a window, where it needs one.

    Deliberately narrow, because the wrong default here is worse than none:
    forcing "x11" on Windows or macOS would break a window that would
    otherwise have opened. It applies only to the one combination measured
    to fail -- a Linux Wayland session, where pygame's own default driver
    raises ``Window framebuffer support not available`` while its x11 driver
    (through XWayland) works. Everywhere else SDL is left to choose, and an
    SDL_VIDEODRIVER already in the environment always wins.
    :return: None.
    """
    if sys.platform != "linux" or os.environ.get("SDL_VIDEODRIVER"):
        return
    on_wayland = (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )
    # No DISPLAY means no XWayland to fall back to, and pointing SDL at an
    # X server that is not there would turn a bad window into no window.
    if not on_wayland or not os.environ.get("DISPLAY"):
        return
    os.environ["SDL_VIDEODRIVER"] = "x11"


def carla_is_listening(
    host: str, port: int, timeout_s: float = PREFLIGHT_TIMEOUT_S
) -> bool:
    """
    Ask whether anything at all is accepting connections on the CARLA port.

    Deliberately weaker than "is there a healthy CARLA server": it is a TCP
    handshake and nothing more, so a server that is still loading a map
    passes and is then waited for normally. It exists to catch the one case
    that otherwise looks like a crash -- **no server started at all** --
    where ``carla.Client`` blocks for its full 120 s timeout inside a C++
    call. Python cannot run a signal handler until that call returns, so the
    demo ignores Ctrl-C for two minutes and prints nothing, because
    ``carla_stdout`` holds fd 1 by then.
    :param host: The CARLA server host.
    :param port: The CARLA server port.
    :param timeout_s: How long to allow the handshake.
    :return: True if the port accepted a connection.
    """
    try:
        with socket.create_connection((host, port), timeout_s):
            return True
    except OSError:
        # Refused, unreachable, unresolvable or too slow -- all of them mean
        # the same thing to the demo, and none of them is worth a traceback.
        return False


def build_provider(args: argparse.Namespace) -> MapProvider:
    """
    Build the map the demo runs on.
    :param args: Parsed command-line arguments.
    :return: A CARLA town, or the synthetic campus map for --backend mock.
    """
    if args.backend == "mock":
        graph = PlanarStreetGraph.generate(
            n_seeds=MOCK_N_SEEDS,
            bounds=MOCK_BOUNDS,
            seed=args.seed,
            stop_density=MOCK_STOP_DENSITY,
        )
        return PlanarMapProvider(graph)
    return CarlaMapProvider(args.town)


def render_size(args: argparse.Namespace) -> tuple[int, int] | None:
    """
    Work out what resolution the chase camera should render at.

    :param args: Parsed command-line arguments.
    :return: The camera resolution, or None to match the window exactly.
    """
    width, height = args.render_size
    if width is None or height is None:
        return None
    return int(width), int(height)


def build_carla_factory(
    args: argparse.Namespace, provider: CarlaMapProvider
) -> Callable[[TransitGraph, str], object]:
    """
    Build the factory that puts the bus in CARLA.

    ``carla`` and the viewer are imported in here rather than at the top of
    the file so that ``--backend mock`` runs without the CARLA client or
    pygame installed at all.
    :param args: Parsed command-line arguments.
    :param provider: The loaded town, read for its stops and speed limits.
    :return: A callable of (graph, start stop) returning a CarlaBackend.
    """
    from src.simulation.CarlaBackend import (
        CarlaBackend, speed_limits_from, stop_labels_from,
        stop_positions_from,
    )

    positions = stop_positions_from(provider)
    speed_for = speed_limits_from(provider)
    # The authored stop IDs, so every label and table row reads "s04"
    # rather than "n0257_n_s04". Passed rather than guessed at -- reading
    # the ID back out of the node name gets TAU wrong.
    labels = stop_labels_from(provider)
    watching = not args.no_viewer

    minimap = None
    if watching:
        from src.simulation.carla_viewer import minimap_from
        minimap = minimap_from(provider)

    def build(graph: TransitGraph, start_stop_id: str) -> CarlaBackend:
        """Build the CARLA backend for this trial."""
        schematic = None
        if minimap is not None:
            from src.simulation.carla_viewer import schematic_from
            schematic = schematic_from(graph, minimap.stops)
        return CarlaBackend(
            stop_positions=positions, position=start_stop_id,
            town=args.town, host=args.host, port=args.port,
            # Price legs in travel time and allow lane changes, matching how
            # TransitGraph is built -- otherwise the policy and the bus
            # disagree about what a leg costs.
            speed_for=speed_for, lane_changes=True,
            fixed_delta_s=DEFAULT_FIXED_DELTA_S, dwell_s=args.dwell,
            npc_vehicles=args.npc_vehicles, seed=args.seed,
            # The viewer needs the world rendered, or the camera it opens
            # shows nothing at all.
            no_rendering=not watching,
            viewer=watching, follow_camera=watching,
            # Drawn by the renderer, so this shows nothing without a
            # window -- which is why it follows `watching` rather than
            # standing on its own.
            debug_draw=watching and not args.no_markers,
            viewer_size=tuple(args.viewer_size),
            render_size=render_size(args), stop_labels=labels,
            # A generated town is a bare ribbon of road with no terrain and
            # no buildings; without this there is nothing on screen but the
            # bus. Pointless with no window open, so it follows `watching`.
            scenery=watching and not args.no_scenery,
            minimap=minimap, schematic=schematic,
            # A demo is often run twice in a row, and CARLA results are not
            # independent of server history. Reloading costs a few seconds
            # and gives every run the same world.
            reload_world=True,
        )

    return build


def describe_setup(
    args: argparse.Namespace, provider: MapProvider, pinned_seed: bool
) -> None:
    """
    Print what is about to run, so the summary can be read in context.
    :param args: Parsed command-line arguments.
    :param provider: The map the trial will run on.
    :param pinned_seed: Whether PYTHONHASHSEED is pinned, so the run could
        be reproduced in another process.
    :return: None.
    """
    where = args.town if args.backend == "carla" else "synthetic campus map"
    print(f"Autobus demo -- {where} on {args.backend}")
    print(f"  policy       {args.policy} -- "
          f"{POLICY_HELP[args.policy]}")
    print(f"  stops        {len(provider.get_stops())}")
    print(f"  service      capacity {args.capacity}, "
          f"window {args.window:.0f} s, patience {args.patience:.0f} s, "
          f"dwell {args.dwell:.0f} s")
    print(f"  demand       {args.max_requests} passengers, one every "
          f"{args.demand_interval:.0f} s")
    print(f"  planning     {args.n_stops} stops ahead, "
          f"{args.commitment} locked in")
    print(f"  ceiling      {args.max_time:.0f} simulated seconds")
    print(f"  seed         {args.seed}"
          + ("" if pinned_seed else "   (see the note below)"))
    if args.backend == "carla":
        print(f"  traffic      {args.npc_vehicles} other vehicles")
        print("  watching     "
              + ("no (--no-viewer)" if args.no_viewer else "yes")
              + ("" if args.no_viewer or args.no_markers
                 else ", with the plan drawn into the world"))
    if not pinned_seed:
        print("\n  Note: PYTHONHASHSEED is not pinned, so another process "
              "may serve\n  these same passengers in a different order. "
              "Fine for a demo; launch\n  with ./run.sh main.py if you "
              "need the run to be reproducible.")


def _seconds(value: float | None) -> str:
    """Format an optional time in seconds for the summary."""
    return "--" if value is None else f"{value:8.1f} s"


def _percent(value: float | None) -> str:
    """Format an optional fraction as a percentage for the summary."""
    return "--" if value is None else f"{100 * value:7.1f} %"


class ProgressLine:
    """
    A one-line live view of a run, redrawn as it goes.

    Bars the requests that have reached a final state, because that is the
    quantity a run is normally trying to exhaust. The clock rides in the
    postfix beside it, since the other way a run can end is by reaching
    ``--max-time`` with demand still outstanding.

    **Writes to stderr on purpose.** ``run_trial(silent=True)`` redirects
    stdout to swallow the runner's own summary, and under CARLA the traffic
    manager's diagnostics have fd 1 rerouted through a pipe -- so a progress
    line on stdout would be captured rather than shown.

    :param total_requests: Passengers the run will create in all.
    :param max_time_s: The simulated-time ceiling.
    """

    # Redraw at most this often, in wall-clock seconds. The observer is
    # called every tick -- thousands of times in a long run -- and repainting
    # a terminal line that fast costs more than the simulation does.
    REDRAW_INTERVAL_S = 0.2

    def __init__(self, total_requests: int, max_time_s: float) -> None:
        self._max_time_s = max_time_s
        self._settled = 0
        self._last_drawn = 0.0
        self.last: TickProgress | None = None
        self._bar = tqdm(
            total=total_requests, unit="req", desc="serving",
            file=sys.stderr, dynamic_ncols=True, leave=True,
        )

    def observe(self, progress: TickProgress) -> None:
        """
        Take one tick's progress and redraw if it is time to.

        :param progress: The tick just completed.
        :return: None.
        """
        self.last = progress
        self._bar.update(progress.settled - self._settled)
        self._settled = progress.settled

        now = time.monotonic()
        if now - self._last_drawn < self.REDRAW_INTERVAL_S:
            return
        self._last_drawn = now
        self._bar.set_postfix_str(self._postfix(progress), refresh=True)

    def _postfix(self, progress: TickProgress) -> str:
        """
        Describe the tick beside the bar.

        :param progress: The tick to describe.
        :return: The postfix text.
        """
        return (
            f"t={progress.sim_time:.0f}/{self._max_time_s:.0f}s "
            f"waiting {progress.pending} aboard {progress.on_bus}"
            + ("" if progress.cancelled == 0
               else f" gave up {progress.cancelled}")
        )

    def close(self) -> None:
        """Finish the bar, leaving the terminal usable."""
        if self.last is not None:
            self._bar.update(self.last.settled - self._settled)
            self._settled = self.last.settled
            # Unthrottled, or a run that finishes between redraws leaves a
            # postfix from seconds ago standing as if it were the result.
            self._bar.set_postfix_str(self._postfix(self.last), refresh=True)
        self._bar.close()


def why_it_ended(
    progress: TickProgress | None, args: argparse.Namespace
) -> str:
    """
    Say which of the two ending conditions actually stopped the run.

    :param progress: The last tick observed, or None if none was.
    :param args: Parsed command-line arguments, for the ceiling.
    :return: A sentence for the report.
    """
    if progress is None:
        return "the run ended."
    if progress.sim_time >= args.max_time - TICK_S:
        outstanding = progress.pending + progress.on_bus
        unborn = args.max_requests - progress.created
        return (
            f"the run hit its --max-time ceiling of {args.max_time:.0f} s "
            f"with {outstanding} passenger(s) still in play and "
            f"{unborn} never created. Unserved demand may simply have run "
            f"out of clock."
        )
    if progress.created < args.max_requests:
        return (
            f"the run ended early: only {progress.created} of "
            f"{args.max_requests} passengers were created."
        )
    return (
        f"every one of the {args.max_requests} passengers reached a final "
        f"state, so the run ended on its own."
    )


def report(
    result: TrialResult,
    args: argparse.Namespace,
    progress: TickProgress | None = None,
    wall_seconds: float | None = None,
) -> None:
    """
    Print what the run achieved, and what the bus actually did.

    The backend counters are printed alongside the KPIs on purpose: on CARLA
    they are how a clean drive is told apart from one that only looks clean
    because the bus was rescued repeatedly.
    :param result: The completed trial.
    :param args: Parsed command-line arguments, for the ceiling check.
    :param progress: The last tick observed, used to say why the run ended.
    :param wall_seconds: Real seconds the run took, if it was timed.
    :return: None.
    """
    kpi = result.kpi
    print("\nHow the service did")
    print(f"  fulfilled          {kpi.fulfilled_count}")
    print(f"  cancelled          {kpi.cancelled_count}")
    print(f"  served fraction  {_percent(kpi.served_fraction)}")
    print(f"  mean wait        {_seconds(kpi.mean_wait_time)}")
    print(f"  p90 wait         {_seconds(kpi.p90_wait_time)}")
    print(f"  max wait         {_seconds(kpi.max_wait_time)}")
    print(f"  mean ride        {_seconds(kpi.mean_ride_time)}")
    print(f"  mean journey     {_seconds(kpi.mean_journey_time)}")
    if kpi.throughput is not None:
        print(f"  throughput       {kpi.throughput:8.2f} /sim-minute")

    if result.backend_stats:
        print("\nWhat the bus did")
        # Width from the longest name present, so a counter added to the
        # backend later cannot silently push the column out of line.
        width = max(len(name) for name in result.backend_stats)
        for name, value in sorted(result.backend_stats.items()):
            print(f"  {name:<{width}} {value:>10.1f}")

    # run_trial ticks at dt = 1.0 s, so one tick is one simulated second.
    sim_time_s = result.tick_count * TICK_S
    print("\nHow it went")
    print(f"  simulated        {_seconds(sim_time_s)}")
    if wall_seconds is not None:
        # The ratio is only worth printing when the run took long enough for
        # it to mean anything; a mock trial finishes in milliseconds and
        # reports a five-figure speed-up that says nothing.
        ratio = ("" if wall_seconds < 1.0
                 else f"   ({sim_time_s / wall_seconds:.0f}x real time)")
        shown = ("under a second" if wall_seconds < 1.0
                 else _seconds(wall_seconds).strip())
        print(f"  real time        {shown}{ratio}")
    print(f"  why it stopped   {why_it_ended(progress, args)}")


def main() -> None:
    """Parse the command line, run one trial, and report on it."""
    args = build_parser().parse_args()

    if args.list_towns:
        print("\n".join(available_towns()))
        return
    if args.list_policies:
        for name in sorted(POLICIES):
            print(f"{name:<12} {POLICY_HELP[name]}")
        return

    unpinned = allow_unpinned_hash()

    # Before the map is loaded, and long before carla.Client blocks: a demo
    # launched against no server should say so at once.
    if (args.backend == "carla"
            and not carla_is_listening(args.host, args.port)):
        sys.exit(f"no CARLA server at {args.host}:{args.port}.\n\n"
                 + NO_SERVER_HELP)

    provider = build_provider(args)
    backend_factory = None
    if args.backend == "carla":
        if not args.no_viewer:
            prefer_x11_on_wayland()
        backend_factory = build_carla_factory(args, provider)

    describe_setup(args, provider, not unpinned)

    progress = ProgressLine(args.max_requests, args.max_time)
    started = time.monotonic()
    try:
        result = run_trial(
            policy_factory=lambda graph: POLICIES[args.policy](
                graph, args
            ),
            provider=provider,
            backend_factory=backend_factory,
            seed=args.seed,
            capacity=args.capacity,
            max_requests=args.max_requests,
            demand_interval_s=args.demand_interval,
            max_wait_s=args.patience,
            dwell_s=args.dwell,
            n_stops=args.n_stops,
            commitment=args.commitment,
            max_time=args.max_time,
            silent=True,
            on_tick=progress.observe,
        )
    except (RuntimeError, OSError) as error:
        progress.close()
        # The overwhelmingly likely cause is no server, and a bare traceback
        # does not say so. Anything else still raises with its traceback.
        if args.backend != "carla":
            raise
        sys.exit(f"the CARLA run failed: {error}\n\n" + NO_SERVER_HELP)
    finally:
        progress.close()
    report(result, args, progress.last, time.monotonic() - started)


if __name__ == "__main__":
    main()
