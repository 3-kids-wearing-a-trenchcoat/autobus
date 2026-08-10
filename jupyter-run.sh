#!/usr/bin/env bash

ENV_NAME="autobus"
SCRIPT_DIR="$(dirname "$(realpath "$0")")"

# Pinned for the same reason as in run.sh: without it, a notebook inherits a
# random hash seed and seeded results cannot be reproduced in a later session.
export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"

PYTHONPATH="$SCRIPT_DIR" conda run -n "$ENV_NAME" --live-stream jupyter lab --no-browser --port=8889
