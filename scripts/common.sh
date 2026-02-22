#!/bin/bash

####################################################################################################
#
# Common script used by other deploy scripts.
#
# This file is meant to be `source`d by a script rather than run directly.
#
####################################################################################################

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

_PROJ_DIR="$( cd -P "$( dirname "$SCRIPT_DIR" )" && pwd )"
export PROJ_DIR=$_PROJ_DIR


if [[ -f "$PROJ_DIR/.env" ]]; then
  set -a
  source "$PROJ_DIR/.env"
  set +a
fi

function create_bucket_if_not_exist() {
  bucket=$1

  bucket_exists=$(aws s3api list-buckets --query 'Buckets[].Name' | jq "any(. == \"$bucket\")")

  if [[ $bucket_exists == "false" ]]; then
    echo "Bucket does not exist, creating now..."
    aws s3api create-bucket --bucket "${bucket}"
    echo "Bucket $bucket created."
  fi
}
