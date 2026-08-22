"""
Fixtures shared by the experiment tests.

``street_graph`` was defined identically in test_campaign and
test_comparison; both run whole campaigns over it, so it stays
module-scoped and is built once per file rather than once per test.
"""

import pytest

from src.map.graph import PlanarStreetGraph


@pytest.fixture(scope="module")
def street_graph() -> PlanarStreetGraph:
    """A small planar map, built once so the suite stays fast."""
    return PlanarStreetGraph.generate(
        n_seeds=12, bounds=(0.0, 0.0, 1000.0, 1000.0), seed=3
    )
