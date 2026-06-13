"""SimulationRunner: top-level driver for the simulation tick loop."""

from src.metrics.MetricsTracker import KPISnapshot
from src.simulation.SimulationBackend import SimulationBackend


class SimulationRunner:
    """
    Drives the simulation tick loop until completion or a time ceiling.

    Callers construct all components (RequestPool, BusAgent, DemandGenerator,
    MetricsTracker, backend) and pass a ready-to-run SimulationBackend.
    SimulationRunner is agnostic to which backend implementation it holds.

    :param backend: Fully initialised SimulationBackend to drive.
    :param dt: Fixed step size in seconds passed to each backend.step() call.
    :param max_time: Safety ceiling in seconds; the loop exits when
        is_done() or sim_time >= max_time, whichever comes first.
    """

    def __init__(
        self,
        backend: SimulationBackend,
        dt: float,
        max_time: float,
    ) -> None:
        self._backend = backend
        self._dt = dt
        self._max_time = max_time

    def run(self) -> KPISnapshot:
        """
        Run the simulation to completion and print a summary to stdout.

        :return: KPISnapshot computed from the final simulation state.
        """
        while (
            not self._backend.is_done()
            and self._backend.sim_time < self._max_time
        ):
            self._backend.step(self._dt)

        kpi = self._backend.metrics.snapshot(self._backend.sim_time)
        _print_results(kpi, self._backend.sim_time)
        return kpi


def _print_results(kpi: KPISnapshot, sim_time: float) -> None:
    """Print a human-readable summary of a completed simulation run."""

    def fmt_float(value: float | None, unit: str = "s") -> str:
        if value is None:
            return "N/A"
        return f"{value:.2f} {unit}"

    print("=== Simulation Complete ===")
    print(f"Sim time:         {sim_time:.2f} s")
    print(f"Fulfilled:        {kpi.fulfilled_count}")
    print(f"Cancelled:        {kpi.cancelled_count}")
    print(f"Mean wait time:   {fmt_float(kpi.mean_wait_time)}")
    print(f"Mean ride time:   {fmt_float(kpi.mean_ride_time)}")
    print(f"Mean journey:     {fmt_float(kpi.mean_journey_time)}")
    if kpi.fulfillment_rate is None:
        print(f"Fulfillment rate: {fmt_float(None)}")
    else:
        print(f"Fulfillment rate: {kpi.fulfillment_rate * 100:.1f} %")
    print(f"Throughput:       {fmt_float(kpi.throughput, 'req/m')}")
