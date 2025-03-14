#!/bin/bash

####################################################################################################
#
# Destroy the demo app's bootstrap.
#
# At the time of this writing, this means destroying the role that the GitLab runner will use to
# deploy.
#
####################################################################################################

set -e
set -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

source "$SCRIPT_DIR/common.sh"

pushd "${PROJ_DIR:?}"
PYTHONPATH=. cdktf destroy kmnlp_bootstrap
popd
