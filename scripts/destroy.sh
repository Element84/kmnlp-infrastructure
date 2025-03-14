#!/bin/bash

####################################################################################################
#
# Destroy the demo app's deployment.
#
####################################################################################################

set -e
set -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

source "$SCRIPT_DIR/common.sh"

pushd "${PROJ_DIR:?}"
PYTHONPATH=. cdktf destroy kmnlp_infra
popd
