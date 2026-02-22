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

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
source "$SCRIPT_DIR/common.sh"

# Usage documentation
function usage() {
    echo "Usage: $(basename "$0") [--auto-approve] [-c|--config <path>] [stack_name]"
    echo ""
    echo "Deploy infrastructure stacks."
    echo ""
    echo "Arguments:"
    echo "  stack_name      Optional. Specific stack to deploy (bootstrap, eks_cluster, dask_cluster, kmnlp_infra)"
    echo "                  If not specified, deploys dask_cluster and kmnlp_infra by default"
    echo ""
    echo "Options:"
    echo "  --auto-approve      Forward to cdktf command to skip interactive approval"
    echo "  -c, --config <path> Path to config YAML file (default: configs/prod.yaml)"
    echo ""
}

auto_approve=""
stack_name=""
export CONFIG_FILE="${CONFIG_FILE:-configs/prod.yaml}"

while test $# != 0
do
  case "$1" in
    --auto-approve)
      auto_approve="--auto-approve";
      shift ;;
    -c|--config)
      if [[ -z "$2" ]] || [[ "$2" == -* ]]; then
        echo "Error: --config requires a path argument"
        usage
        exit 1
      fi
      export CONFIG_FILE="$2"
      shift 2 ;;
    bootstrap|eks_cluster|dask_cluster|kmnlp_infra)
      stack_name="$1"
      shift ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$stack_name" ]] && [[ "$1" != -* ]]; then
        # Assume it's a stack name if it doesn't start with -
        case "$1" in
          bootstrap|eks_cluster|dask_cluster|kmnlp_infra)
            stack_name="$1"
            shift ;;
          *)
            echo "Error: Unknown stack name '$1'"
            echo "Valid stack names: bootstrap, eks_cluster, dask_cluster, kmnlp_infra"
            exit 1
            ;;
        esac
      else
        echo "Error: Unknown option '$1'"
        usage
        exit 1
      fi
      ;;
  esac
done

# Load constants from config file.
export STATE_BUCKET
STATE_BUCKET=$(yq -r .terraform.state_bucket "$CONFIG_FILE")
export DEPLOY_BUCKET
DEPLOY_BUCKET=$(yq -r .terraform.deploy_bucket "$CONFIG_FILE")
export LOCK_TABLE
LOCK_TABLE=$(yq -r .terraform.lock_table "$CONFIG_FILE")
export AWS_REGION
AWS_REGION=$(yq -r .aws_account.region "$CONFIG_FILE")
# Set default region for boto3.
export AWS_DEFAULT_REGION="$AWS_REGION"

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
export PYTHONPATH=.

if [[ -n "$stack_name" ]]; then
    echo "Deploying specific stack: $stack_name"
    cdktf deploy ${auto_approve+$auto_approve} "$stack_name"
else
    echo "Deploying default stacks: dask_cluster and kmnlp_infra"
    cdktf deploy ${auto_approve+$auto_approve} dask_cluster
    cdktf deploy ${auto_approve+$auto_approve} kmnlp_infra
fi

popd
