import os
import logging
import time
import subprocess
from typing import TypedDict

import aiodocker
from aiodocker import DockerError

# Registry config from environment
MF_PUSH_TO_REGISTRY = os.environ.get("MF_PUSH_TO_REGISTRY", "false").lower() in ("true", "1")
MF_PULL_FROM_REGISTRY = os.environ.get("MF_PULL_FROM_REGISTRY", "false").lower() in ("true", "1")

MF_REGISTRY_URL = os.environ.get("MF_REGISTRY_URL", None)
MF_REGISTRY_USER = os.environ.get("MF_REGISTRY_USER", None)
MF_REGISTRY_PASS = os.environ.get("MF_REGISTRY_PASS", None)

logged_in = False

def login_to_registry(url: str, username: str, password: str) -> None:
    """Login to Docker Hub using the provided username and password."""
    global logged_in
    if not logged_in:
        login_cmd = f'echo "{password}" | docker login {url} -u "{username}" --password-stdin'
        try:
            subprocess.run(login_cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Failed to login to {url}: {e}")
            raise e
        logged_in = True
        print(f"Logged in to {url} successfully.")

class DockerRegisteryConfig(TypedDict):
    """Docker registry configuration.

    Args:
        url: URL of the Docker registry.
        user: Username to authenticate to the registry.
        password: Password to authenticate to the registry.
    """
    push_to_registry: bool
    pull_from_registry: bool
    registry_url: str
    registry_user: str
    registry_pass: str

GLOBAL_REGISTRY_CONFIG = DockerRegisteryConfig(
    push_to_registry=MF_PUSH_TO_REGISTRY,
    pull_from_registry=MF_PULL_FROM_REGISTRY,
    registry_url=MF_REGISTRY_URL,
    registry_user=MF_REGISTRY_USER,
    registry_pass=MF_REGISTRY_PASS
)

async def get_from_existing_image(
    client: aiodocker.Docker,
    image_name: str,
) -> str:
    """Get the image name from existing images."""
    images = await client.images.list()
    existing_images = [img['RepoTags'] for img in images if img.get('RepoTags')]
    for tags in existing_images:
        if not tags:
            continue
        for tag in tags:
            if image_name in tag:
                return tag
    return None

def get_registry_img_name(
    image_name: str,
    registery_config: DockerRegisteryConfig,
) -> str:
    """Get the registry image name."""
    assert registery_config['registry_user'], "Registry user must be provided"
    if registery_config['registry_url'] and registery_config['registry_url'] != 'docker.io':
        return f"{registery_config['registry_url']}/{image_name}"
    return f"{registery_config['registry_user']}/{image_name}"

async def push_img_to_registry(client: aiodocker.Docker, image_name: str, registery_config: DockerRegisteryConfig, logger: logging.Logger) -> None:
    """Push an image to Docker Hub."""
    try:
        push_start = time.perf_counter()

        registry_url = registery_config['registry_url']
        registry_image_name = get_registry_img_name(image_name, registery_config)
        await client.images.tag(image_name, registry_image_name)

        # Login to Docker Hub
        login_to_registry(
            registry_url if registry_url else '',
            registery_config['registry_user'],
            registery_config['registry_pass']
        )
        
        # Actually push the image
        logger.info(f"Pushing image {registry_image_name} to {registry_url if registry_url else 'docker.io'}...")
        push_logs = await client.images.push(registry_image_name, auth={
            'username': registery_config['registry_user'],
            'password': registery_config['registry_pass']
        })
        for log_msg in push_logs:
            if 'status' in log_msg:
                logger.debug(log_msg['status'])
            elif 'error' in log_msg:
                logger.error(f"Error pushing image: {log_msg['error']}")
                raise Exception(f"Failed to push image: {log_msg['error']}")

        logger.info(f"Successfully pushed {registry_image_name} in {time.perf_counter() - push_start:.2f} seconds")
    except Exception as e:
        logger.error(f"Failed to push image:\n {e}")
        raise e
    finally:
        try:
            await client.images.delete(get_registry_img_name(image_name, registery_config), force=True)
        except DockerError as e:
            pass

async def pull_img_from_registry(client: aiodocker.Docker, image_name: str, registery_config: DockerRegisteryConfig, logger: logging.Logger) -> str:
    """Pull an image from Docker Hub."""

    pull_start = time.perf_counter()
    registry_image_name = get_registry_img_name(image_name, registery_config)

    logger.info(f"Pulling image {registry_image_name} from registry...")

    pull_logs = await client.images.pull(registry_image_name, auth={
        'username': registery_config['registry_user'],
        'password': registery_config['registry_pass']
    })
    for log_msg in pull_logs:
        if 'status' in log_msg:
            logger.debug(log_msg['status'])
        elif 'error' in log_msg:
            logger.error(f"Error pulling image: {log_msg['error']}")
            raise DockerError(f"Failed to pull image {image_name}: {log_msg['error']}")

    await client.images.tag(registry_image_name, image_name)
    try:
        await client.images.delete(registry_image_name, force=True)
    except DockerError as e:
        pass

    logger.info(f"Successfully pulled {registry_image_name} in {time.perf_counter() - pull_start:.2f} seconds")
    return registry_image_name

async def push_local_images():
    registry = "10.10.100.19:5000"
    GLOBAL_REGISTRY_CONFIG['registry_url'] = registry
    GLOBAL_REGISTRY_CONFIG['registry_user'] = "bmc"
    GLOBAL_REGISTRY_CONFIG['registry_pass'] = "bmc"
    
    login_to_registry(
        registry,
        GLOBAL_REGISTRY_CONFIG['registry_user'],
        GLOBAL_REGISTRY_CONFIG['registry_pass']
    )
    client = aiodocker.Docker()
    await pull_img_from_registry(
        client,
        "hello-world",
        GLOBAL_REGISTRY_CONFIG,
        logging.getLogger("push_local_images")
    )

    await client.close()
    
if __name__ == "__main__":
    import asyncio
    asyncio.run(push_local_images())