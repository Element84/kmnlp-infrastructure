#!/bin/bash
set -e -o pipefail

####################################################################################################
# Uploads the front end code to the website bucket to deploy it.
#
# Arguments
# * artifact_s3_uri - S3 URI containing the tarred frontend code.
# * website_bucket - Bucket to copy the frontend to
# * api_url - URL of the API. A configuration file is generated to point the frontend at the API.
####################################################################################################

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
source "$SCRIPT_DIR/common.sh"

# Validate required parameters
if [[ $# -ne 3 ]]; then
    echo "Error: Expected 3 parameters, got $#"
    echo "Usage: $0 <artifact_s3_uri> <website_bucket> <api_url>"
    exit 1
fi

if [[ -z "$1" ]] || [[ -z "$2" ]] || [[ -z "$3" ]]; then
    echo "Error: All parameters must be non-empty"
    echo "Usage: $0 <artifact_s3_uri> <website_bucket> <api_url>"
    exit 1
fi

artifact_s3_uri=$1
website_bucket=$2
api_url=$3


TEMP_DIR="${PROJ_DIR:?}/temp"
mkdir -p "$TEMP_DIR"

# Download artifact and metadata
aws s3 cp "$artifact_s3_uri" "$TEMP_DIR/frontend.tar.gz"

# Extract
rm -rf "$TEMP_DIR/frontend-deploy"
mkdir -p "$TEMP_DIR/frontend-deploy"
tar -xzf  "$TEMP_DIR/frontend.tar.gz" -C "$TEMP_DIR/frontend-deploy"

# Create runtime config
cat > "$TEMP_DIR/frontend-deploy/config.js" <<EOF
// Runtime configuration - generated at deployment
// Deployed: $(date -u +%Y-%m-%dT%H:%M:%SZ)
window.APP_CONFIG = {
  apiUrl: '${api_url}',
};
EOF

# Deploy to S3
aws s3 sync \
  "$TEMP_DIR/frontend-deploy" \
  "s3://${website_bucket}/" \
  --delete \
  --cache-control "public, max-age=300, immutable"

echo "Frontend deployment completed successfully"
