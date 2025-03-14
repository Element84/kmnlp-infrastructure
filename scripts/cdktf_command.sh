#!/bin/bash

set -e
set -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

source "$SCRIPT_DIR/common.sh"

cdktf_command=$1

pushd "${PROJ_DIR:?}"
PYTHONPATH=. cdktf "$cdktf_command" kmnlp_infra
popd
