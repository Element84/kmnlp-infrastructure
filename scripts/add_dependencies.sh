#!/bin/bash

##############################################
# Add dependencies to the virtual environment.
##############################################

set -eu -o pipefail

uv add "$@"
uv export --no-hashes --all-extras --format requirements-txt > requirements.txt
uv sync --all-extras
