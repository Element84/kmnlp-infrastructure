#!/bin/bash

####################################################################################################
#
# Execute a CDKTF command with needed env variables.
#
####################################################################################################

set -e
set -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
source "$SCRIPT_DIR/common.sh"

# Usage documentation
function usage() {
    echo "Usage: $(basename "$0") [-c|--config <path>] [cdktf_command] [stack_name] [-- cdktf_options...]"
    echo ""
    echo "Deploy infrastructure stacks."
    echo ""
    echo "Arguments:"
    echo "  cdktf_command   Required. CDKTF command to run (e.g. deploy, plan, synth, ...)"
    echo "  stack_name      Optional. Specific stack to deploy (bootstrap, eks_cluster, dask_cluster, kmnlp_infra)"
    echo "                  If not specified, deploys dask_cluster and kmnlp_infra by default"
    echo ""
    echo "Options:"
    echo "  -c, --config <path> Path to config YAML file (default: configs/prod.yaml)"
    echo "  --                  Separator between script options and cdktf options"
    echo "                      All arguments after -- are forwarded to cdktf"
    echo ""
    echo "Examples:"
    echo "  $(basename "$0") deploy kmnlp_infra -- --auto-approve --hcl"
    echo "  $(basename "$0") -c configs/dev.yaml plan -- --output dist/"
    echo "  $(basename "$0") diff -- --hcl"
    echo ""
}

cdktf_command=""
stack_name=""
cdktf_args=()
export CONFIG_FILE="${CONFIG_FILE:-configs/prod.yaml}"

while test $# != 0
do
  case "$1" in
    -c|--config)
      if [[ -z "$2" ]] || [[ "$2" == -* ]]; then
        echo "Error: --config requires a path argument"
        usage
        exit 1
      fi
      export CONFIG_FILE="$2"
      shift 2 ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      cdktf_args=("$@")
      break
      ;;
    *)
      if [[ "$1" != -* ]]; then
        # Positional argument (cdktf_command or stack_name)
        if [[ -z $cdktf_command ]]; then
          cdktf_command=$1
          shift
        elif [[ -z $stack_name ]]; then
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
          echo "Error: Unexpected argument '$1'"
          usage
          exit 1
        fi
      else
        echo "Error: Unknown option '$1'"
        echo "Use -- to separate script options from cdktf options"
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z $cdktf_command ]]; then
  echo "Error: No CDKTF command provided."
  usage
  exit 1
fi

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
    echo "Run command $cdktf_command on stack: $stack_name"
    cdktf "$cdktf_command" "$stack_name" "${cdktf_args[@]}"
else
    echo "Running command $cdktf_command on default stacks: dask_cluster and kmnlp_infra"
    cdktf "$cdktf_command" dask_cluster "${cdktf_args[@]}"
    cdktf "$cdktf_command" kmnlp_infra "${cdktf_args[@]}"
fi

popd
