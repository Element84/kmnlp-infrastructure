#!/bin/bash

####################################################################################################
# Performs linting on shellscripts
####################################################################################################

set -e -o pipefail


# Define array of shellcheck codes to exclude
declare -a SHELLCHECK_EXCLUDE=(
    "SC1090"  # Can't follow non-constant source
    "SC1091"  # Not following: source file not found
    "SC2002"  # Useless cat
    "SC2230"  # Which is non-standard. Use builtin command -v
    "SC2250"  # Prefer putting braces around variable references
    "SC2310"  # shellcheck (version) will run on shell scripts only
    "SC2311"  # Shell directive is unknown/unsupported by shellcheck
    "SC2312"  # Consider invoking this command separately to avoid masking its return value
)

# Build exclude arguments
EXCLUDE_ARGS=""
for code in "${SHELLCHECK_EXCLUDE[@]}"; do
    EXCLUDE_ARGS="${EXCLUDE_ARGS} -e ${code%%[[:space:]]*}"
done

# Execute shellcheck with constructed arguments
# shellcheck disable=SC2086
find . -name "*.sh" ! -path "./.venv/*" -exec \
  shellcheck \
  --severity=style \
  --enable=all \
  -x \
  ${EXCLUDE_ARGS} \
  {} +
