#!/bin/bash

####################################################################################################
#
# Deploy app infrastructure.
#
####################################################################################################

set -e
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
"$SCRIPT_DIR/cdktf_command.sh" deploy "$@"
