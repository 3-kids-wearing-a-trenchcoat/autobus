#!/usr/bin/env bash
set -e

ENV_NAME="autobus"

# Ensure conda is available
if ! command -v conda &> /dev/null; then
    echo "Conda not found. Please install Miniforge/Anaconda."
    exit 1
fi

# Run inside the correct environment
conda run --no-capture-output -n "$ENV_NAME" mkdocs serve -f autobus-docs/mkdocs.yml