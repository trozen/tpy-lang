# Nightly CI image. One parameterized file covers the whole Linux matrix: BASE_IMAGE picks the distro (which folds the Python
# version and glibc/libstdc++ into the same axis), TOOLCHAIN_PKGS the
# compiler, EXTRA_PKGS per-config additions (e.g. system C deps for the
# system-deps row). The zig config installs no compiler packages at all --
# zig comes from the ziglang wheel via `uv sync --extra bundled`.
ARG BASE_IMAGE=ubuntu:24.04
ARG UV_VERSION=latest
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${BASE_IMAGE}
ARG TOOLCHAIN_PKGS=""
ARG EXTRA_PKGS=""
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-venv ccache \
        ${TOOLCHAIN_PKGS} ${EXTRA_PKGS} \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
# The distro Python IS the point of the BASE_IMAGE axis (24.04 -> 3.12,
# 26.04 -> its default) -- never let uv substitute a managed download.
ENV UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never
