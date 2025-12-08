FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    git \
    jq \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3 \
    python3-pip \
    wget \
    unzip \
    ca-certificates \
    libicu70 \
    liblttng-ust1 \
    libkrb5-3 \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash runner

WORKDIR /home/runner

ARG RUNNER_VERSION="2.311.0"
RUN curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L \
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
    && tar xzf ./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
    && rm actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
    && chown -R runner:runner /home/runner

COPY start.sh /home/runner/start.sh

RUN chmod +x /home/runner/start.sh && chown runner:runner /home/runner/start.sh

USER runner

CMD ["/home/runner/start.sh"]
