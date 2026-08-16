#!/usr/bin/env bash
#
# Install CARLA's `agents` package into this project's conda environment.
#
# `agents.navigation` -- BasicAgent, LocalPlanner, VehiclePIDController --
# is what drives the bus along a route we planned. It is **not on PyPI**:
# the `carla` wheel is client bindings only (`__init__.py`, `libcarla.so`
# and two `.pyi` stubs). The package exists only inside the CARLA image, as
# ordinary Python over the same public API.
#
# It is deliberately **not vendored into this repository**. Copying it here
# would make ~1,800 lines of someone else's code part of the project, and
# there is nothing to be gained by owning it: taking it from the pinned
# image instead means it can never drift out of step with the server, and it
# is a dependency like any other. The trade is that `conda env create` alone
# does not fully build the environment -- this script is the second step.
#
# Idempotent: re-running replaces the installed copy.

set -e

ENV_NAME="autobus"
CARLA_IMAGE="carlasim/carla:0.9.16"
SOURCE="/workspace/PythonAPI/carla/agents"

if ! command -v conda &> /dev/null; then
    echo "Conda not found. Please install Miniforge/Anaconda." >&2
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo "Docker not found; the agents package is extracted from the CARLA image." >&2
    exit 1
fi

# Ask the environment where its own site-packages is, rather than guessing a
# python version into the path.
SITE_PACKAGES="$(conda run -n "$ENV_NAME" python -c \
    'import site; print(site.getsitepackages()[0])')"

if [ -z "$SITE_PACKAGES" ] || [ ! -d "$SITE_PACKAGES" ]; then
    echo "Could not locate site-packages for conda env '$ENV_NAME'." >&2
    exit 1
fi

echo "Pulling $CARLA_IMAGE if it is not already present..."
docker image inspect "$CARLA_IMAGE" > /dev/null 2>&1 || docker pull "$CARLA_IMAGE"

CONTAINER="carla-agents-extract-$$"
docker create --name "$CONTAINER" "$CARLA_IMAGE" > /dev/null
trap 'docker rm "$CONTAINER" > /dev/null 2>&1 || true' EXIT

rm -rf "${SITE_PACKAGES:?}/agents"
docker cp "$CONTAINER:$SOURCE" "$SITE_PACKAGES/"

echo "Installed CARLA agents into $SITE_PACKAGES/agents"
conda run -n "$ENV_NAME" python -c "
from agents.navigation.basic_agent import BasicAgent
from agents.navigation.local_planner import RoadOption
print('import OK:', BasicAgent.__module__, '/', RoadOption.__name__)
"
