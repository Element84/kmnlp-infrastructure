#!/bin/bash

####################################################################################################
# Perform bootstrapping steps that must be in place for the deploy to work.
#
# At the time of this writing, this mean creating the deploy role that the GitLab runner will assume
# during the deploy process.
####################################################################################################


set -e
set -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

source "$SCRIPT_DIR/common.sh"

create_bucket_if_not_exist "${STATE_BUCKET:?}"
create_bucket_if_not_exist "${DEPLOY_BUCKET:?}"

table_exists=$(aws dynamodb list-tables --query 'TableNames' | jq "any(. == \"${LOCK_TABLE:?}\")")

if [[ "$table_exists" == "false" ]]; then
  aws dynamodb create-table \
    --table-name "$LOCK_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
  echo "$LOCK_TABLE Table has been created."
fi

pushd "${PROJ_DIR:?}"
PYTHONPATH=. cdktf apply kmnlp_bootstrap
popd
