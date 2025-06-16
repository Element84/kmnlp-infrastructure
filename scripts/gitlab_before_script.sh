#!/bin/bash

####################################################################################################
# Sets up the gitlab runner docker image to install additional things needed for build steps.
####################################################################################################

set -e -o pipefail

# Install requirements needed to install GPG key
apt-get update && apt-get install -y gnupg software-properties-common wget

# Install Hashicorp GPG key
wget -O - https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/hashicorp.list

# Install necessary dependencies.
apt-get update && \
  apt-get install -y \
  curl \
  unzip \
  shellcheck \
  jq \
  npm \
  terraform

npm install --global cdktf-cli@latest terraform@latest

# Install the AWS CLi
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
./aws/install

# Install uv
pip install uv

# Create the venv and install dependencies.
uv sync --all-extras
