ARG BASE_IMAGE=ghcr.io/hostinger/hvps-hermes-agent:latest
FROM ${BASE_IMAGE}

USER root
WORKDIR /opt/runtime-footer-build
COPY runtime_footer_patch.py verify_installed.py ./
RUN /opt/hermes/.venv/bin/python runtime_footer_patch.py --target /opt/hermes \
 && /opt/hermes/.venv/bin/python verify_installed.py --target /opt/hermes \
 && rm -rf /opt/runtime-footer-build

LABEL org.opencontainers.image.title="Hermes Agent with Codex quota footer" \
      org.opencontainers.image.source="https://github.com/mate5630-debug/hermes-runtime-footer"
