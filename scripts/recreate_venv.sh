#!/bin/bash

####################################################################################################
# Recreates the virtual environment with frozen dependencies.
####################################################################################################

set -e -o pipefail

rm -r .venv
uv sync --all-extras
