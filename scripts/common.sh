#!/bin/bash

####################################################################################################
#
# Common script used by other deploy scripts.
#
# This file is meant to be `source`d by a script rather than run directly.
#
# Executing this script will set:
#   * Environment variables based on the `.env` file,
#   * SCRIPT_DIR and PROJ_DIR
#   * ACCOUNT_ID based `aws sts get-caller-identity`
#
# It will also provide a function to create an S3 bucket.
#
####################################################################################################

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

export PROJ_DIR="$SCRIPT_DIR/.."


set -a
source "$PROJ_DIR/.env"
set +a


export ACCOUNT_ID
ACCOUNT_ID=$(aws sts get-caller-identity | jq -r '.Account')

function create_bucket_if_not_exist() {
  bucket=$1

  bucket_exists=$(aws s3api list-buckets --query 'Buckets[].Name' | jq "any(. == \"$bucket\")")

  if [[ $bucket_exists == "false" ]]; then
    echo "Bucket does not exist, creating now..."
    aws s3api create-bucket --bucket "${bucket}"
    echo "Bucket $bucket created."
  fi
}
