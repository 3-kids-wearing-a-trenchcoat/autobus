#!/usr/bin/env bash
#
# The demo, without the ceremony: `./autobus.sh --policy fixed` is exactly
# `./run.sh main.py --policy fixed`, and every flag main.py takes is passed
# straight through. `./autobus.sh --help` lists them.
#
# This delegates to run.sh rather than repeating it, so the environment name
# and the variables that make a run reproducible are defined in one place and
# this file cannot drift out of step with them.
set -e

# Resolved rather than assumed, so the demo can be started from anywhere and
# still finds the main.py sitting beside this script.
SCRIPT_DIR="$(dirname "$(realpath "$0")")"

exec "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/main.py" "$@"
