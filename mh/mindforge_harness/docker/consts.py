"""Constants for Dockerfile generation."""
import os

GREEN_ZONE_CERTIFICATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certificates')
PANDAS_INSTALLATION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'additional')

GREEN_ZONE_CERTIFICATES = """
COPY hwweb.pem hwweb.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV http_proxy={http_proxy}
ENV https_proxy={https_proxy}
"""

DOCKER_FILE_R2E = """
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y ca-certificates

# Certificates
{certificates}

RUN echo "tzdata tzdata/Areas select Asia" | debconf-set-selections && echo "tzdata tzdata/Zones/Asia select Hong_Kong" | debconf-set-selections

ARG OLD_COMMIT
RUN apt-get update -y && apt-get upgrade -y
RUN apt-get install ca-certificates -y


ENV DEBIAN_FRONTEND noninteractive


# Install standard and python specific system dependencies
RUN apt-get install -y git curl wget build-essential ca-certificates python3-dev

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.cargo/bin:${{PATH}}"
ENV PATH="/root/.local/bin:${{PATH}}"

RUN git clone https://github.com/pandas-dev/pandas.git testbed

COPY run_tests.sh /testbed/run_tests.sh

COPY install.sh /testbed/install.sh

WORKDIR /testbed

RUN git checkout $OLD_COMMIT

RUN git status

RUN bash install.sh

RUN uv pip install tree_sitter_languages

ENV VIRTUAL_ENV=/testbed/.venv

ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY r2e_tests /r2e_tests
"""

DOCKER_FILE = """
FROM python:{python}-slim

# Certificates
{certificates}

# Set environment variables
ENV PATH="/root/.local/bin:${{PATH}}"
ENV WORKSPACE="/workspace"

# Install system dependencies, uv, create a virtual environment, and install Python dependencies in a single RUN command
RUN apt-get update && apt-get install -y --no-install-recommends \
    git time wget patch && \
    rm -rf /var/lib/apt/lists/* && \
    wget -qO- https://astral.sh/uv/install.sh | sh && \
    uv pip install --system --no-cache-dir setuptools wheel && \
    mkdir -p $WORKSPACE

# Set working directory
WORKDIR $WORKSPACE

# Copy necessary files
COPY ./patch_codes.py /app/
COPY {repo_path} $WORKSPACE

# Run pre-installation and installation commands in a single step
RUN {pre_install} && {pip_install}
RUN uv pip install --system --no-cache-dir pytest pytest-json-report pytest-timeout
"""

EVAL_SCRIPT = """#!/bin/bash

set -x

# Patch pytest-json-report
sed -i '230s/.*/            root=str(session.fspath if "fspath" in session.__dict__ else session.path),/' /usr/local/lib/{pyversion}/site-packages/pytest_jsonreport/plugin.py

time git checkout $GIT_COMMIT

python /app/patch_codes.py

time {install} > /results/install_log.txt 2> /results/install_log.txt

{eval_commands}

# Some debug info
ls
cd /workspace

time {test_cmd} > /results/test_log.txt 2> /results/test_err.txt
"""

PATCH_CODE_PY = """import subprocess
import sys
import time
from pathlib import Path

WORK_DIR = '/workspace'

def apply_patch():
    patch_dir = Path("/patches")

    if not patch_dir.exists():
        print("⚠️ No patch directory found at /patch")
        return

    # Get sorted list of patch files
    patch_files = sorted(patch_dir.glob("*.patch"))

    if not patch_files:
        print("ℹ️ No .patch files found in /patch directory")
        return

    print(f"🔧 Applying {len(patch_files)} patches from /patch")

    for patch_file in patch_files:
        try:
            result = subprocess.run(
                ["patch", "-p1", "--no-backup-if-mismatch", "-i", str(patch_file)],
                cwd=WORK_DIR,
                check=True,
                capture_output=True,
                text=True
            )
            print(f"✓ Applied {patch_file.name}")
            if result.stdout:
                print(f"   Output: {result.stdout.strip()}")

        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to apply {patch_file.name}")
            print(f"   STDOUT: {e.stdout.strip()}")
            print(f"   STDERR: {e.stderr.strip()}")
            print(f"   Return code: {e.returncode}")

            # Attempt to reverse already applied patches
            print("🔄 Attempting to rollback applied patches...")
            _rollback_patches(patch_files, applied_up_to=patch_file)

            sys.exit(1)

def _rollback_patches(patch_files, applied_up_to):
    idx = patch_files.index(applied_up_to)
    for patch in reversed(patch_files[:idx+1]):
        try:
            subprocess.run(["echo", str(patch), "> tmp.patch"])
            subprocess.run(
                ["patch", "-R", "-p1", "--no-backup-if-mismatch", "-i", "tmp.patch"],
                cwd=WORK_DIR,
                check=True
            )
            print(f"   ↪ Reverted {patch.name}")
        except subprocess.CalledProcessError:
            print(f"   ⚠️ Failed to revert {patch.name}")

if __name__ == "__main__":
    start_time = time.perf_counter()
    apply_patch()
    print(f"🕒 Patching completed in {time.perf_counter() - start_time:.2f} seconds")
"""

# Hash the Dockerfile and patch code to generate a unique identifier for the Docker image
# DOCKER_IMAGE_COMBINED = (DOCKER_FILE + GREEN_ZONE_CERTIFICATES + PATCH_CODE_PY).encode()
DOCKER_IMAGE_COMBINED = (DOCKER_FILE_R2E + GREEN_ZONE_CERTIFICATES + PATCH_CODE_PY).encode()