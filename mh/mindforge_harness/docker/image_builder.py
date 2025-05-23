import asyncio
import os
import tempfile
import time
from typing import Iterable
from collections import defaultdict

import aiodocker
import orjson

from aiodocker.exceptions import DockerError

from mindforge_harness.logger import TQDMLogger, MindForgeHarnessLogger
from mindforge_harness.utils import (
    create_tarball,
    consistent_hash,
    get_cached_or_clone_repo,
)
from mindforge_harness.docker.consts import (
    GREEN_ZONE_CERTIFICATES,
    # DOCKER_FILE,
    DOCKER_FILE_R2E,
    GREEN_ZONE_CERTIFICATES_DIR,
    PATCH_CODE_PY,
    PANDAS_INSTALLATION_DIR
)
from mindforge_harness.docker.docker_utils import (
    DockerRegisteryConfig,
    GLOBAL_REGISTRY_CONFIG,
    get_registry_img_name,
    get_from_existing_image,
    push_img_to_registry,
    pull_img_from_registry,
)

# Global lock to track ongoing builds
image_build_locks = defaultdict(asyncio.Lock)
failed_images = set()

def format_dockerfile(
    repo_path: str,
    python_version: str,
    pip_packages: list[str],
    packages: str,
    pre_install: list[str],
    green_zone: bool=False
) -> str:
    """Format Dockerfile according to your specs."""
    if green_zone:
        http_proxy = os.environ.get("http_proxy", "")
        https_proxy = os.environ.get("https_proxy", "")
        certificates = GREEN_ZONE_CERTIFICATES.format(
            http_proxy=http_proxy,
            https_proxy=https_proxy
        )
    else:
        certificates = ""
    # Build up the pip install commands
    pip_install = ""
    if packages:
        pip_install += f"uv pip install --system -U {packages}\n"
    if pip_packages:
        tmp = ' '.join(f'\"{pkg}\"' for pkg in pip_packages)
        pip_install += f"uv pip install --system -U {tmp}\n"
    if not pre_install:
        pre_install = ['true']
    elif "apt-get update" not in pre_install:
        pre_install.insert(0, "apt-get update")
    template_vars_dockerfile = {
        "repo_path": repo_path,
        "python": python_version if not python_version.startswith('python') else python_version[:6],
        "pre_install": " && ".join(pre_install),
        "pip_install": pip_install,
        "certificates": certificates
    }
    # return DOCKER_FILE.format(**template_vars_dockerfile)
    try:
        return DOCKER_FILE_R2E.format(**template_vars_dockerfile)
    except Exception as e:
        print(str(e))
        return

def get_image_name(repo_name: str, spec_dict: dict) -> str:
    """Get a unique Docker Hub-compatible image name.

    <dockerhub-user>/<something-unique>:<tag> (if you need a tag)
    For simplicity, we'll do no explicit tag, just "latest".
    """
    repo_name = repo_name.lower()
    spec_hash = consistent_hash(spec_dict)
    return f"eval-{repo_name.replace('/', '-')}-{spec_hash[:8]}"

async def build_docker_image_from_specs(
    client: aiodocker.Docker,
    repo_name: str,
    spec_dict: dict,
    docker_work_dir: str,
    force_rebuild: bool=False,
    green_zone: bool=False,
    registry_config: DockerRegisteryConfig=None,
) -> str:
    """Build or get a Docker image from your specs."""
    image_name = get_image_name(repo_name, spec_dict)

    async with image_build_locks[image_name]:
        if image_name in failed_images:
            raise Exception(f"Failed to build image {image_name} before. Skipping the build.")

        # Use TQDMLogger for your logging
        with TQDMLogger(f'build-{image_name}', os.path.join(docker_work_dir, "build-or-fetch.log")) as logger:

            if not force_rebuild:
                if await get_from_existing_image(client, image_name):
                    logger.debug(f"Image {image_name} found locally. No need to build.")
                    return image_name

                # --------------------------
                # Attempt to PULL from Docker Hub first (if pull_from_registry=True)
                # IMPORTANT: image_name ALREADY has "byelegumes/..." so no double prefix
                # --------------------------
                if registry_config and registry_config["pull_from_registry"]:
                    try:
                       return pull_img_from_registry(
                            client,
                            image_name,
                            registry_config,
                            logger
                        )
                    except DockerError as e:
                        logger.info(f"Image not found in Registry or pull failed: {e}. Will build locally...")

            # --------------------------
            # Build the image
            # --------------------------
            logger.info(f"Image {image_name} not found locally, building...")
            build_start = time.perf_counter()

            # Setup directory for building
            build_dir = os.path.join(docker_work_dir, image_name)
            os.makedirs(build_dir, exist_ok=True)
            logger.info(f"Logs will be saved in {build_dir}")

            for key in ['execute_test_as_nonroot', 'nano_cpus', 'no_use_env']:
                if key in spec_dict:
                    logger.warning(f"Key {key} not yet supported in MindForge Harness.")

            pip_packages = spec_dict.get("pip_packages", [])
            if not pip_packages:
                logger.warning(f"No pip packages found in the spec for {image_name}.")

            # Create Dockerfile
            repo_path = get_cached_or_clone_repo(repo_name, clean_cache=force_rebuild,)
            logger.debug(f"Cloned repo to {repo_path}")
            
            formatted_docker = format_dockerfile(
                repo_path=os.path.basename(repo_path),
                python_version=spec_dict['python'],
                pip_packages=pip_packages,
                packages=spec_dict.get("packages"),
                pre_install=spec_dict.get("pre_install"),
                green_zone=green_zone
            )
            with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
                f.write(formatted_docker)

            logger.debug(f"Formatted Dockerfile:\n{formatted_docker}")

            # Write patch_codes.py
            patch_script_path = os.path.join(build_dir, "patch_codes.py")
            with open(patch_script_path, "w") as f:
                f.write(PATCH_CODE_PY)

            # Create a tarball of the build directory
            tar_stream = create_tarball(
                build_dir, repo_path,
                [
                    os.path.join(GREEN_ZONE_CERTIFICATES_DIR, "hwweb.crt"),
                    os.path.join(GREEN_ZONE_CERTIFICATES_DIR, "hwweb.pem"),
                    os.path.join(PANDAS_INSTALLATION_DIR, "install.sh"),
                    os.path.join(PANDAS_INSTALLATION_DIR, "run_tests.sh"),
                ] if green_zone else []
            )

            # Build logs from aiodocker
            build_logs = client.images.build(
                fileobj=tar_stream,
                tag=image_name,
                encoding="gzip",
                forcerm=True,
                rm=True,
                stream=True,
            )

            # Capture logs to build.log
            with MindForgeHarnessLogger(f"build-{image_name}", os.path.join(build_dir, "build.log"), add_stdout=True) as build_logger:
                try:
                    async for log in build_logs:
                        if 'stream' in log:
                            build_logger.debug(log['stream'].strip())
                        elif 'error' in log:
                            build_logger.error(f"Error: {log['error']}")
                            failed_images.add(image_name)
                            raise Exception(f"Build failed: {log['error']}.\n"
                                            f"For more details, check the logs at {build_dir}")
                except DockerError as e:
                    build_logger.error(f"Failed to fetch the logs while building {image_name}:\n {e}")
                    raise e
                except TimeoutError:
                    error = f"Build timeout after {client.session.timeout.total} seconds."
                    build_logger.error(error)
                    raise TimeoutError(error)

            # Cleanup
            os.remove(patch_script_path)

            logger.info(f"Built Docker image {image_name} in {time.perf_counter() - build_start:.2f} seconds.")

            # --------------------------
            # Push to Docker Hub if requested
            # --------------------------
            if registry_config and registry_config["push_to_registry"]:
                await push_img_to_registry(client, image_name, registry_config, logger)

            return image_name

async def build_all_images(data_path: str, log_dir: str=None, force_build=False, green_zone=False):
    """Build all images from a data file. Each line in data file is a JSON object with 'repo' & 'spec_dict'."""
    client = aiodocker.Docker()

    with open(data_path) as f:
        data = [orjson.loads(line) for line in f]

    # If no log_dir is provided, use a temp path
    docker_work_dir = log_dir or tempfile.mkdtemp()
    os.makedirs(docker_work_dir, exist_ok=True)

    for instance in data:
        repo = instance['repo']
        spec_dict = instance['spec_dict']
        await build_docker_image_from_specs(
            client,
            repo,
            spec_dict,
            docker_work_dir,
            force_rebuild=force_build,
            green_zone=green_zone,
            registry_config=GLOBAL_REGISTRY_CONFIG,
        )
    await client.close()

async def build_a_spec(repo: str, spec_dict: dict, log_dir: str=None, force_build=False, green_zone=False):
    """Build a single spec."""
    client = aiodocker.Docker()
    docker_work_dir = log_dir or tempfile.mkdtemp()
    os.makedirs(docker_work_dir, exist_ok=True)
    await build_docker_image_from_specs(
        client,
        repo,
        spec_dict,
        docker_work_dir,
        force_rebuild=force_build,
        green_zone=green_zone,
        registry_config=GLOBAL_REGISTRY_CONFIG,
    )
    await client.close()

async def clean_up_images(client: aiodocker.Docker, images_to_remove: Iterable[str]=image_build_locks.keys()):
    """Clean up images that are not in the image_build_locks."""
    for image in images_to_remove:
        if image in failed_images:
            continue
        
        if name := get_from_existing_image(client, image):
            try:
                await client.images.delete(name, force=True)
                print(f"Removed image {name}")
            except DockerError as e:
                print(f"Failed to remove image {name}: {e}")

