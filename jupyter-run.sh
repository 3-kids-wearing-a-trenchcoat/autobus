#!/usr/bin/env bash

ENV_NAME="autobus"
SCRIPT_DIR="$(dirname "$(realpath "$0")")"

PYTHONPATH="$SCRIPT_DIR" conda run -n "$ENV_NAME" jupyter lab