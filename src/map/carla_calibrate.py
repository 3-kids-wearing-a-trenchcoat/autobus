"""Calibrate a dumped town against how a bus actually drives it.

Run once per town, against a live server, after ``carla_dump``::

    ./run.sh -m src.map.carla_calibrate --town Town01 --write

**This module imports carla**, like ``carla_dump`` and unlike everything else
in the map layer.

## What it measures, and why both halves are needed

The graph estimates ``length / speed_limit`` — free flow, with no notion of a
traffic light. Time stationary at reds is what the correction mostly absorbs:
measured 2026-08-16 under direct drive, **32% of leg time on Town01 rising to
55% on Town05**.

Driving each leg several times separates two error terms that a campaign's
aggregate counters cannot:

* **within-leg spread** — execution luck, the thing ``NoisyTravelModel``
  models and ``safety_factor`` hedges. **0.04 on Town01 but 0.19 on Town02**
  under direct drive at twenty NPCs, against 0.028 for the whole set under
  the old turn-list steering. The bus no longer overtakes — the traffic
  manager changed lanes around slow vehicles, `BasicAgent` queues behind
  them — so a leg's duration now depends on the traffic it happens to meet.
* **between-leg spread** — the estimator being wrong by *different* amounts
  on different legs. **0.24 on Town01 to 0.55 on Town03**, and it is not
  noise. A safety margin cannot repair it; only a better estimate can.

⚠ Both spreads are per-town and neither transfers. Quoting one town's figure
for another is the mistake ``traffic_efficiency`` itself exists to prevent.

So the correction belongs in the map data, as ``traffic_efficiency``, and
that is what ``--write`` records.

## It also checks the speed limits

``carla_dump.read_speed_limits`` reads per-road limits from type-274
landmarks, and a sign governs its road only from where it stands. This drives
the town and compares the dumped table against what the vehicle itself
reports through ``get_speed_limit()``, so a wrong table is caught here rather
than showing up later as a policy that inexplicably mis-times a leg.
"""

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict

from tqdm.auto import tqdm
from pathlib import Path

from src.algorithm.TravelModel import GraphTravelModel
from src.map.carla_topology import (
    CarlaTopology,
    load_topology,
    save_topology,
    town_name,
)
from src.map.CarlaMapProvider import CarlaMapProvider
from src.map.TransitGraph import TransitGraph
from src.simulation.CarlaBackend import (
    CarlaBackend, speed_limits_from, stop_positions_from,
)
from src.algorithm.RequestPool import RequestPool
from src.simulation.SimulationBackend import (
    AdvanceResult,
    SimulationBackend,
)

# Ticks between speed-limit samples. At 0.05 s a bus covers a couple of
# metres in this, which is far finer than the roads being checked.
SPEED_SAMPLE_EVERY = 40

# A leg still running after this did not arrive, and its timing is
# meaningless; the tour continues so one bad leg does not lose the run.
#
# Sized against the legs these towns actually have, which is the whole
# difficulty: a cap below the real span does not protect a calibration, it
# silently *biases* it, because the legs that breach it are the long ones
# and dropping them fits the factor to the short ones. At 900 s this
# discarded 8 of 30 legs on Town03 and 12 of 30 on Town05 — yet Town05's
# slowest measured leg is 1120 s and arrives perfectly well, and Town03 has
# legs reaching 3000 s. Measured 2026-08-12 across four towns; the same
# mistake at 200 s once manufactured "4 of 25 legs fail" on Town01.
LEG_TIMEOUT_S = 3000.0


def geo_stats(values: list[float]) -> tuple[float, float]:
    """
    Fit a lognormal to a set of ratios.
    :param values: Strictly positive ratios.
    :return: (geometric mean — the bias, sigma of the log — the spread).
    """
    logs = [math.log(value) for value in values]
    sigma = statistics.stdev(logs) if len(logs) > 1 else 0.0
    return math.exp(statistics.fmean(logs)), sigma


def drive_tour(
    backend: CarlaBackend,
    order: list[str],
    travel_model: GraphTravelModel,
    tour: int,
    verbose: bool = True,
) -> tuple[list[dict], list[tuple[int, float]]]:
    """
    Drive one loop through every stop, timing each leg against its estimate.
    :param backend: A started CarlaBackend.
    :param order: Stop node IDs, driven in sequence and back to the first.
    :param travel_model: The model whose predictions are being calibrated.
    :param tour: Which repeat this is, recorded on each leg.
    :param verbose: Print each leg as it completes.
    :return: (per-leg records, (road_id, speed limit) pairs seen driving).
    """
    legs: list[dict] = []
    observed: list[tuple[int, float]] = []
    sequence = order + [order[0]]
    carla_map = backend._world.get_map()

    for origin, target in zip(sequence, sequence[1:]):
        predicted = travel_model.travel_time(origin, target)
        before = backend.stats()
        elapsed = 0.0
        ticks = 0

        while elapsed < LEG_TIMEOUT_S:
            # Re-issued every pass, exactly as SimulationRunner does.
            backend.set_destination(target)
            result = backend.advance(backend._delta)
            elapsed += result.consumed
            ticks += 1
            if ticks % SPEED_SAMPLE_EVERY == 0:
                bus = backend._bus()
                if bus is not None:
                    waypoint = carla_map.get_waypoint(bus.get_location())
                    observed.append(
                        (int(waypoint.road_id), float(bus.get_speed_limit()))
                    )
            if result.arrived_at is not None:
                break

        after = backend.stats()
        arrived = elapsed < LEG_TIMEOUT_S
        legs.append({
            "tour": tour,
            "leg": f"{origin}->{target}",
            "arrived": arrived,
            "predicted_s": predicted,
            "actual_s": elapsed,
            "distance_m": after["distance_m"] - before["distance_m"],
            "red_s": ((after["red_light_ticks"] - before["red_light_ticks"])
                      * backend._delta),
        })
        if verbose:
            flag = "" if arrived else "  !! TIMED OUT"
            print(f"  tour {tour} {origin}->{target:<14} "
                  f"pred {predicted:6.1f}s  actual {elapsed:6.1f}s  "
                  f"ratio {elapsed / predicted:5.2f}"
                  f"  red {legs[-1]['red_s']:5.1f}s{flag}")
    return legs, observed


def check_speed_limits(
    observed: list[tuple[int, float]], topology: object
) -> dict[str, object]:
    """
    Compare the dumped speed-limit table against what the vehicle read.

    A road is counted once, at the limit most often reported while the bus
    was on it — sampling is dense, so a single mid-road sign change would
    show up as a road that disagrees rather than as noise.
    :param observed: (road_id, limit) pairs sampled while driving.
    :param topology: The loaded CarlaTopology whose table is being checked.
    :return: Agreement counts and every road that disagreed.
    """
    by_road: dict[int, Counter] = defaultdict(Counter)
    for road_id, limit in observed:
        by_road[road_id][limit] += 1

    agree, disagree = 0, []
    for road_id, seen in by_road.items():
        actual = seen.most_common(1)[0][0]
        ours = topology.speed_limit_for(road_id)
        if abs(ours - actual) < 0.5:
            agree += 1
        else:
            disagree.append({"road_id": road_id, "dumped": ours,
                             "vehicle_reported": actual,
                             "samples": sum(seen.values())})
    return {
        "roads_driven": len(by_road),
        "agree": agree,
        "disagree": sorted(disagree, key=lambda d: -d["samples"]),
        "values_seen": sorted({limit for _, limit in observed}),
    }


def calibrate(
    town: str, tours: int, npc_vehicles: int, host: str, port: int,
    verbose: bool = True,
) -> dict[str, object]:
    """
    Drive a town and report how its estimates compare with the driving.
    :param town: Town to calibrate; must already be dumped and authored.
    :param tours: Loops of every stop. Two or more separates execution noise
        from model error; one measures only their sum.
    :param npc_vehicles: Traffic to spawn. Execution noise is near zero in an
        empty town, so a realistic figure needs some.
    :param host: CARLA server host.
    :param port: CARLA server port.
    :param verbose: Print each leg as it completes.
    :return: The full report, ready for JSON.
    """
    provider = CarlaMapProvider(town)
    graph = TransitGraph(provider)
    travel_model = GraphTravelModel(graph, dwell_s=0.0)
    positions = stop_positions_from(provider)
    order = sorted(positions)

    backend = CarlaBackend(
        stop_positions=positions, position=order[0], town=town,
        speed_for=speed_limits_from(provider), lane_changes=True,
        host=host, port=port, npc_vehicles=npc_vehicles, seed=0,
        no_rendering=True,
    )
    started = time.perf_counter()
    backend.start()
    if verbose:
        print(f"{town}: {len(order)} stops, {tours} tours = "
              f"{len(order) * tours} legs, npc_vehicles={npc_vehicles} "
              f"(started in {time.perf_counter() - started:.1f}s)\n")

    legs: list[dict] = []
    observed: list[tuple[int, float]] = []
    try:
        for tour in range(tours):
            tour_legs, tour_observed = drive_tour(
                backend, order, travel_model, tour, verbose
            )
            legs.extend(tour_legs)
            observed.extend(tour_observed)
    finally:
        backend.close()

    return summarise(town, legs, observed, npc_vehicles, tours)


def summarise(
    town: str, legs: list[dict], observed: list[tuple[int, float]],
    npc_vehicles: int, tours: int,
) -> dict[str, object]:
    """
    Turn driven legs into the two error terms and the efficiency factor.
    :param town: Town that was driven.
    :param legs: Per-leg records from drive_tour.
    :param observed: (road_id, limit) pairs sampled while driving.
    :param npc_vehicles: Traffic the tour was driven in.
    :param tours: How many loops were driven.
    :return: The report.
    """
    good = [leg for leg in legs if leg["arrived"] and leg["predicted_s"] > 0]
    if not good:
        raise RuntimeError("no leg arrived; nothing to calibrate from")

    ratios = [leg["actual_s"] / leg["predicted_s"] for leg in good]
    bias, sigma_pooled = geo_stats(ratios)

    by_leg: dict[str, list[float]] = defaultdict(list)
    for leg in good:
        by_leg[leg["leg"]].append(leg["actual_s"] / leg["predicted_s"])
    repeated = [values for values in by_leg.values() if len(values) > 1]
    within = (statistics.fmean(geo_stats(v)[1] for v in repeated)
              if repeated else float("nan"))
    between = (geo_stats([geo_stats(v)[0] for v in repeated])[1]
               if len(repeated) > 1 else float("nan"))

    total_time = sum(leg["actual_s"] for leg in good)
    total_red = sum(leg["red_s"] for leg in good)
    moving = [(leg["actual_s"] - leg["red_s"]) / leg["predicted_s"]
              for leg in good]

    return {
        "town": town_name(town),
        "npc_vehicles": npc_vehicles,
        "tours": tours,
        "legs_arrived": len(good),
        "legs_attempted": len(legs),
        # The number that goes into the dump: estimates are `bias` times too
        # fast, so the achieved fraction of the posted limit is its inverse.
        "traffic_efficiency": round(1.0 / bias, 4),
        "bias": round(bias, 4),
        "sigma_pooled": round(sigma_pooled, 4),
        "sigma_within_leg": round(within, 4),
        "sigma_between_legs": round(between, 4),
        "bias_excluding_red_lights": round(geo_stats(moving)[0], 4),
        "red_light_fraction": round(total_red / total_time, 4),
        "effective_speed_mps": round(
            sum(leg["distance_m"] for leg in good) / total_time, 3),
        "speed_limits": check_speed_limits(observed, load_topology(town)),
        "legs": good,
    }


def report(summary: dict[str, object]) -> None:
    """
    Print a calibration summary.
    :param summary: What ``calibrate`` returned.
    :return: None.
    """
    limits = summary["speed_limits"]
    print(f"\n{'=' * 66}")
    print(f"CALIBRATION — {summary['town']}, "
          f"{summary['legs_arrived']}/{summary['legs_attempted']} legs, "
          f"{summary['npc_vehicles']} NPCs")
    print("=" * 66)
    print(f"bias (actual / predicted)        {summary['bias']:8.3f}x")
    print(f"  excluding red-light time       "
          f"{summary['bias_excluding_red_lights']:8.3f}x")
    print(f"  time stationary at red         "
          f"{100 * summary['red_light_fraction']:8.1f}%")
    print(f"effective speed                  "
          f"{summary['effective_speed_mps']:8.2f} m/s")
    print(f"sigma within leg (noise)         "
          f"{summary['sigma_within_leg']:8.3f}")
    print(f"sigma between legs (model error) "
          f"{summary['sigma_between_legs']:8.3f}")
    print(f"\n-> traffic_efficiency            "
          f"{summary['traffic_efficiency']:8.4f}")
    print(f"\nspeed limits: {limits['agree']}/{limits['roads_driven']} roads "
          f"driven agree with the dump; values seen {limits['values_seen']}")
    for row in limits["disagree"][:10]:
        print(f"  road {row['road_id']}: dumped {row['dumped']} km/h, "
              f"vehicle reported {row['vehicle_reported']} km/h "
              f"({row['samples']} samples)")


class LegRecordingBackend(SimulationBackend):
    """
    Wraps a backend and times every leg against what the graph predicted.

    Exists because the fixed tour ``calibrate`` drives is not the route a
    policy takes: measured on Town01, a campaign averages 3.77 m/s against
    the tour's 4.21, so a factor fitted on the tour is ~10% optimistic for
    the legs a policy actually chooses. This records the same
    predicted-versus-actual pairs while the *policy* decides where to go.

    Delegates rather than subclasses so the recorded run drives exactly the
    backend a campaign drives.
    """

    def __init__(
        self, inner: SimulationBackend, travel_model: GraphTravelModel
    ) -> None:
        """
        :param inner: The backend actually driving.
        :param travel_model: The model whose predictions are being checked.
            Must be the one the policy plans against, so the measured ratio
            is against the estimate in force rather than a fresh one.
        """
        # Set first: __getattr__ delegates through it.
        self._inner = inner
        self._travel_model = travel_model
        self.legs: list[dict] = []
        self._destination: str | None = None
        self._origin: str | None = None
        self._elapsed = 0.0

    parallel_safe = False

    def set_destination(self, stop_id: str) -> None:
        """
        Start timing a new leg when the destination changes, then delegate.
        :param stop_id: Stop to drive to.
        :return: None.
        """
        if stop_id != self._destination:
            self._destination = stop_id
            self._origin = self._inner.current_location()
            self._elapsed = 0.0
        self._inner.set_destination(stop_id)

    def advance(self, dt: float) -> AdvanceResult:
        """
        Delegate, accumulate, and close out the leg on arrival.
        :param dt: Seconds of simulation to advance.
        :return: Whatever the wrapped backend returned.
        """
        result = self._inner.advance(dt)
        self._elapsed += result.consumed
        if result.arrived_at is not None:
            origin = self._origin
            if origin is not None and origin != result.arrived_at:
                predicted = self._travel_model.travel_time(
                    origin, result.arrived_at
                )
                if predicted > 0.0:
                    self.legs.append({
                        "leg": f"{origin}->{result.arrived_at}",
                        "predicted_s": predicted,
                        "actual_s": self._elapsed,
                    })
            # A re-issued destination must start a fresh leg, so forget it.
            self._destination = None
            self._elapsed = 0.0
        return result

    def current_location(self) -> str:
        """Delegate; the wrapper holds no position of its own."""
        return self._inner.current_location()

    # SimulationBackend gives start, close and sync_passengers **concrete
    # no-op defaults**, so they resolve on the base class and __getattr__ is
    # never consulted for them. Without these three the wrapped backend is
    # never started and never cleaned up -- which fails as a null world on
    # the first tick.
    def start(self) -> None:
        """Start the wrapped backend."""
        self._inner.start()

    def close(self) -> None:
        """Close the wrapped backend."""
        self._inner.close()

    def sync_passengers(self, pool: "RequestPool") -> None:
        """
        Delegate passenger rendering.
        :param pool: The request pool to mirror into the world.
        :return: None.
        """
        self._inner.sync_passengers(pool)

    def __getattr__(self, name: str) -> object:
        """
        Delegate everything else -- start, close, sync_passengers, stats.
        :param name: Attribute being looked up.
        :return: The wrapped backend's attribute.
        """
        return getattr(self._inner, name)


def calibrate_policy_driven(
    town: str, seeds: list[int], npc_vehicles: int, host: str, port: int,
    trial_kwargs: dict, policy_factory=None, verbose: bool = True,
) -> dict[str, object]:
    """
    Calibrate against the legs a *policy* chooses, not a fixed tour.

    The number this produces is the one a campaign lives under, and it is
    the one to record. ``calibrate`` remains useful for a town with no
    authored demand scenario yet, and for separating execution noise from
    model error, which needs the same leg driven repeatedly.
    :param town: Town to drive; must be dumped, authored and calibrated.
    :param seeds: Trial seeds to run. Three is usually plenty — the residual
        being corrected is a bias, not a noise term.
    :param npc_vehicles: Traffic to spawn.
    :param host: CARLA server host.
    :param port: CARLA server port.
    :param trial_kwargs: The campaign's own trial parameters, so the legs
        measured are the legs the campaign drives.
    :param policy_factory: Builds the policy to follow. Defaults to None,
        which uses ReorderingInsertionPolicy at the trial's own window.
    :param verbose: Print each trial as it completes.
    :return: The measured bias and the efficiency it implies.
    """
    from src.algorithm.ReorderingInsertionPolicy import (
        ReorderingInsertionPolicy,
    )
    from src.experiment.trial import run_trial

    provider = CarlaMapProvider(town)
    positions = stop_positions_from(provider)
    current = provider.traffic_efficiency()
    window = trial_kwargs.get("max_wait_s", 750.0)
    if policy_factory is None:
        def policy_factory(graph):
            """The recommended policy at the campaign's own window."""
            return ReorderingInsertionPolicy(graph, max_wait_s=window)

    recorders: list[LegRecordingBackend] = []
    # run_trial does not hand the seed to the backend factory, so it is
    # carried here: CARLA's traffic and spawn placement are luck of their
    # own, and holding them fixed would under-sample the variance.
    current_seed = [0]

    def backend_factory(graph, start_stop_id: str) -> SimulationBackend:
        """Build the campaign's backend, wrapped in a leg recorder."""
        inner = CarlaBackend(
            stop_positions=positions, position=start_stop_id, town=town,
            speed_for=speed_limits_from(provider), lane_changes=True,
            host=host, port=port, npc_vehicles=npc_vehicles,
            seed=current_seed[0], no_rendering=True,
            dwell_s=trial_kwargs.get("dwell_s", 0.0),
        )
        recorder = LegRecordingBackend(
            inner, GraphTravelModel(TransitGraph(provider),
                                    dwell_s=trial_kwargs.get("dwell_s", 0.0))
        )
        recorders.append(recorder)
        return recorder

    legs: list[dict] = []
    for seed in tqdm(seeds, desc=f"{town} policy-driven", unit="trial",
                     disable=not verbose):
        recorders.clear()
        current_seed[0] = seed
        run_trial(policy_factory, provider=provider, seed=seed,
                  backend_factory=backend_factory, silent=True,
                  **trial_kwargs)
        legs.extend(recorders[0].legs)

    ratios = [leg["actual_s"] / leg["predicted_s"] for leg in legs]
    bias, sigma = geo_stats(ratios)
    return {
        "town": town_name(town),
        "mode": "policy-driven",
        "seeds": list(seeds),
        "legs": len(legs),
        "current_efficiency": current,
        "residual_bias": round(bias, 4),
        "sigma": round(sigma, 4),
        # The estimate in force is already scaled by `current`; a residual
        # bias of b means it is still b times too fast, so divide again.
        "suggested_efficiency": round(current / bias, 4),
    }


def main(argv: list[str] | None = None) -> int:
    """
    Calibrate one town and optionally write the result into its dump.
    :param argv: Command-line arguments, or None to read sys.argv.
    :return: Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--town", default="Town01")
    parser.add_argument("--tours", type=int, default=3)
    parser.add_argument("--npc-vehicles", type=int, default=20)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--out", type=Path, default=None,
                        help="Write the full report here as JSON.")
    parser.add_argument("--write", action="store_true",
                        help="Record traffic_efficiency into the town's dump.")
    arguments = parser.parse_args(argv)

    summary = calibrate(
        arguments.town, arguments.tours, arguments.npc_vehicles,
        arguments.host, arguments.port,
    )
    report(summary)

    if arguments.out is not None:
        arguments.out.write_text(json.dumps(summary, indent=1))
        print(f"\nwrote report {arguments.out}")

    if arguments.write:
        topology = load_topology(arguments.town)
        # Recalibrating a town that already carries a factor would otherwise
        # compound: the drive was made under the factor already in force.
        combined = topology.traffic_efficiency * summary["traffic_efficiency"]
        updated = CarlaTopology(
            map=topology.map,
            default_speed_limit_kph=topology.default_speed_limit_kph,
            speed_limit_overrides=topology.speed_limit_overrides,
            segments=topology.segments,
            traffic_efficiency=round(combined, 4),
        )
        path = save_topology(updated, note=(
            f"traffic_efficiency {combined:.4f} calibrated over "
            f"{summary['legs_arrived']} legs at {summary['npc_vehicles']} "
            f"NPCs; previous factor {topology.traffic_efficiency}"))
        print(f"wrote {path} with traffic_efficiency={combined:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
