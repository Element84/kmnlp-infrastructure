#!/bin/bash

####################################################################################################
# Performs code linting, type checks, and formatting checks. Fails if errors are found.
####################################################################################################

set -e -o pipefail

echo "Running shellcheck"
scripts/shellcheck.sh
echo "---"

echo "Running Ruff"
ruff check kmnlp_infra/
echo "---"

echo "Running pyright"
pyright kmnlp_infra/
echo "---"

echo "Checking if code has been formatted with Ruff"
if ! ruff format --check kmnlp_infra/; then
    echo "ERROR: Code has not been formatted with Ruff. Please run: ruff format."
    exit 1
fi
echo "---"

echo "All linting checks passed!"
