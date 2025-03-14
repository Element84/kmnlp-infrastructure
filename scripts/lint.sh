#!/bin/bash

####################################################################################################
# Performs code linting and type checks. Fails if errors are found
####################################################################################################

set -e -o pipefail

echo "Running shellcheck"
scripts/shellcheck.sh

echo "Running ruff"
ruff check .

echo "Running pyright"
pyright .
