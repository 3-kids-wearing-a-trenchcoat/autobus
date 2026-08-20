#!/usr/bin/env bash
set -e

ENV_NAME="autobus"

# Pinned so that seeded runs reproduce across processes. Several policies take
# min() over a set[str] and break ties by iteration order, which depends on
# Python's per-process string hash randomisation -- without this,
# FixedRoutePolicy generates a literally different cycle from the same graph in
# a different process. Export your own value beforehand to override.
export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"

# Print as it happens rather than in one lump at the end. Two separate
# buffers sit between a print and the terminal, and both have to go:
# `conda run` captures the child's streams and replays them on exit, and
# Python itself block-buffers stdout whenever it is a pipe rather than a
# terminal. A long run looked completely silent until it finished, and a run
# that was killed -- or that crashed a CARLA server -- lost everything it had
# printed, which is exactly when the output is worth most.
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

# Ensure conda is available
if ! command -v conda &> /dev/null; then
    echo "Conda not found. Please install Miniforge/Anaconda."
    exit 1
fi

# Run inside the correct environment
conda run --no-capture-output -n "$ENV_NAME" python "$@"
