#!/bin/bash

####################################################################################################
#
# Deploy the Demo app.
#
# This script deploys only the Chainlit/app infra.
#
####################################################################################################

set -e
set -o pipefail

# Usage documentation
function usage() {
    echo "Usage: $(basename "$0") [--auto-approve]"
    echo ""
    echo "Deploy the kmnlp_infra stack."
    echo ""
    echo "If the --auto-approve flag is provided, it gets forwarded to the cdktf command."
    echo ""
}

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

auto_approve=""
while test $# != 0
do
  case "$1" in
    --auto-approve)
      auto_approve="--auto-approve";
      shift ;;
    *)
      usage
      exit 1
      ;;
  esac
done

pushd "${PROJ_DIR:?}"
PYTHONPATH=. cdktf apply ${auto_approve+$auto_approve} kmnlp_infra
popd
