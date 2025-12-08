#!/bin/bash

# Runner para repositório específico
./config.sh \
    --url "https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}" \
    --token "${GITHUB_TOKEN}" \
    --name "${RUNNER_NAME:-railway-runner}" \
    --work "_work" \
    --labels "railway,self-hosted,linux,x64" \
    --unattended \
    --replace

./run.sh
