# Autolycus Agent — Docker Image
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/NikolayGusev-astra/autolycus"
LABEL org.opencontainers.image.description="Autolycus Agent — Enterprise AI Assistant"
LABEL org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    git curl tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash autolycus

WORKDIR /home/autolycus

# Clone repo at build time
RUN git clone --depth=1 https://github.com/NikolayGusev-astra/autolycus.git repo \
    && python3 -m venv venv \
    && . venv/bin/activate \
    && cd repo \
    && pip install -q -e ".[cli,gateway,cron]" \
    && rm -rf ~/.cache/pip

# Config directory
RUN mkdir -p .autolycus
COPY config.yaml .autolycus/config.yaml

EXPOSE 8500

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["./venv/bin/python3", "-m", "hermes_gateway.run"]
