#!/usr/bin/env bash
set -e

ENV_NAME="autobus"

# Pinned so that seeded runs reproduce across processes. Several policies take
# min() over a set[str] and break ties by iteration order, which depends on
# Python's per-process string hash randomisation -- without this,
# FixedRoutePolicy generates a literally different cycle from the same graph in
# a different process. Export your own value beforehand to override.
export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"

# Ensure conda is available
if ! command -v conda &> /dev/null; then
    echo "Conda not found. Please install Miniforge/Anaconda."
    exit 1
fi

# Run inside the correct environment
conda run -n "$ENV_NAME" python "$@"
