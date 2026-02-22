#!/bin/bash

####################################################################################################
# Recreates the virtual environment with frozen dependencies.
####################################################################################################

set -e -o pipefail

rm -r .venv || true
uv sync --all-extras
