# syntax=docker/dockerfile:1
ARG PI_BASE_IMAGE=pi-agent-wildclawbench-1.0:latest
ARG NODE_BASE_IMAGE=node:24-bookworm-slim
FROM ${NODE_BASE_IMAGE} AS node_runtime

FROM ${PI_BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/root \
    TERM=xterm-256color \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PI_CODING_AGENT_DIR=/tmp_workspace/.pi/agent \
    PI_CODING_AGENT_SESSION_DIR=/tmp_workspace/.pi/agent/sessions

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        libffi-dev \
        libjpeg-dev \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        python3 \
        python-is-python3 \
        python3-pip \
        python3-venv \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node_runtime /usr/local/ /usr/local/
RUN node --version \
    && npm --version \
    && npm config set allow-scripts=agent-browser --location=user

COPY requirements.lock /tmp/requirements.lock
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

COPY pi_launcher.py /usr/local/bin/pi_launcher.py
RUN chmod 0755 /usr/local/bin/pi_launcher.py \
    && pi --version \
    && python3 -c "import fitz, requests, weasyprint, yaml"

RUN mkdir -p /tmp_workspace \
    && mv /root/.pi /tmp_workspace/.pi

RUN --mount=type=secret,id=pi_settings,required=true \
    install -D -m 0600 /run/secrets/pi_settings /tmp_workspace/.pi/agent/settings.json \
    && python3 -c "import json; from pathlib import Path; p=Path('/tmp_workspace/.pi/agent/settings.json'); d=json.loads(p.read_text()); d['extensions']=['/tmp_workspace/.pi']; p.write_text(json.dumps(d, indent=2) + '\\n')"

WORKDIR /tmp_workspace
CMD ["/bin/bash"]
