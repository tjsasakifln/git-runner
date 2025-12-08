#!/bin/bash

# Runner em nível de conta (serve todos os repos)
./config.sh \
    --url "https://github.com/${GITHUB_OWNER}" \
    --token "${GITHUB_TOKEN}" \
    --name "${RUNNER_NAME:-railway-runner}" \
    --work "_work" \
    --labels "railway,self-hosted,linux,x64" \
    --unattended \
    --replace

./run.sh
