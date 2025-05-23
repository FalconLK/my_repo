"""Docker utilities for building and running containers."""
import asyncio
import logging
import os
import shlex
import time

from pathlib import Path
from uuid import uuid4

import aiodocker
import orjson

# ------------------------
# CHANGES: We removed double prefixing from the push code.
# ------------------------
from mindforge_harness.docker.consts import (
    EVAL_SCRIPT,
)
from mindforge_harness.consts import (
    UNITEST_TIMEOUT_MAX,
)
from mindforge_harness.docker.docker_utils import (
    DockerRegisteryConfig,
    GLOBAL_REGISTRY_CONFIG,
)
from mindforge_harness.docker.image_builder import (
    build_docker_image_from_specs,
)
from mindforge_harness.logger import MindForgeHarnessLogger

class EvaluationPipelineInterface:
    """Interface for building and running evaluation pipelines.

    How this works,
    Overload the format_eval_script method to create a custom eval script, which will be run in the container.
    Overload the gather_results method, which tasks the docker mount directory and returns the results.
    log_dir
    ├── docker (cotains the eval.sh and patch files)
    ├── results (contains the pytest_report.json. You should save the results here. i.e. pytest > /results/pytest_report.json)
    ├── run_instance.log

    """

    def format_eval_script(
        self,
        tests: list[str],
        test_cmd: str,
        eval_commands: list[str],
        install: str,
        timeout: int,
        pyversion: str,
        failfast: bool,
    ) -> str:
        """Format the evaluation script."""
        # FIXME: Ideally, this should be copied during the docker build.
        # However, I don't know how to properly format the environment variables to pass the test names
        if not tests:
            test_cmd = f"echo 'No test to run' && {test_cmd} my_dummy_test_that_has_a_very_unique_name.py" # We run it anyway with some dummy command to get a test report
        else:
            if failfast and '-x ' not in test_cmd and '--exitfirst' not in test_cmd:
                test_cmd += ' --exitfirst'
            test_cmd = test_cmd if 'json-report' in test_cmd else test_cmd + " " + "--tb=short --json-report --json-report-file=/pass_report.json -W ignore::DeprecationWarning"
            test_cmd = test_cmd + f" --timeout {timeout} " + ' '.join([shlex.quote(x) for x in tests])
        install_cmd = install.replace('pip install', 'pip install --no-deps --no-build-isolation')
        template_vars_entrypoint = {
            "install": install_cmd,
            "test_cmd": test_cmd,
            "eval_commands": "\n".join(eval_commands),
            "pyversion": pyversion,
        }
        return EVAL_SCRIPT.format(**template_vars_entrypoint)

    def gather_results(self, log_dir: str, logger: logging.Logger, tests: list[str], skipped_ok=True, short=True, ignore_collector_errors=True) -> dict:
        """Gather results from the test logs."""
        # Load results
        result_file = os.path.join(log_dir, "results", "pytest_report.json")
        with open(result_file) as f:
            if not f.read(1):
                # Check if the stderr is empty:
                with open(os.path.join(log_dir, "results/test_err.txt")) as f:
                    if not f.read(1):
                        raise Exception(f"Error while generating the test reports. Are you running pytest using --json-report? Check the logs in {log_dir} for more information.")
                    else:
                        f.seek(0)
                        error = f.read()
                        logger.error(error)
                        raise Exception(f"Error while generating the test reports. \n{error}")
            f.seek(0)
            results = orjson.loads(f.read())

        # Summaries
        if not skipped_ok:
            short_results = {x['nodeid']: (x['outcome'] == 'passed')
                                for x in results['tests']}
        else:
            short_results = {x['nodeid']: (x['outcome'] in ['passed', 'skipped'])
                                for x in results['tests']}

        # Validate tests
        problematic_collectors = {}
        if tests and 'collectors' in results:
            for collector in results['collectors']:
                if collector['outcome'] == 'failed':
                    error = f"Failed to collect tests: {collector['nodeid']}"
                    logger.error(error)
                    logger.error(collector['longrepr'])
                    # Always raise an error if the collector failed with an ModuleNotFoundError
                    # Since ignore error is only used in produce, import error cannot be ignored as it's definitely a problem with spec dict.
                    if 'ModuleNotFoundError' in collector['longrepr'] or "ImportError" in collector['longrepr']:
                        raise Exception(f"Failed to collect tests due to an ImportError: {collector['nodeid']}\n{collector['longrepr']}")
                    problematic_collectors[collector['nodeid']] = collector['longrepr']
            if problematic_collectors and not ignore_collector_errors:
                raise Exception(f"Failed to collect tests: {problematic_collectors}")

        if tests and len(results['tests']) < len(tests):
            tests_missing = [t for t in tests if t not in short_results]
            logger.warning(f"Tests missing in the test report: {tests_missing}")
            for test in tests_missing:
                short_results[test] = False
                if not short:
                    longrepr = None
                    for pcollectors in problematic_collectors.keys():
                        if pcollectors in test:
                            longrepr = problematic_collectors[pcollectors]
                            break
                    results['tests'].append(compose_a_report_for_missing_test(test, longrepr))

        logger.info(f"Results: {short_results}")
        return short_results if short else results

DEFAULT_PIPELINE = EvaluationPipelineInterface()

def compose_a_report_for_missing_test(test: str, longrepr: str=None) -> dict:
    """Compose a report for missing test."""
    return {
        "nodeid": test,
        "lineno": 0,
        "outcome": "error",
        "keywords": [],
        "setup": {
            "duration": 0.0,
            "outcome": "failed",
            "longrepr": f"{test} not found in the working directory."
        },
        "teardown": {
            "duration": 0.0,
            "outcome": "skipped",
            "longrepr": f"{test} not found in the working directory."
        }
    } if not longrepr else {
        "nodeid": test,
        "lineno": 0,
        "outcome": "error",
        "keywords": [],
        "setup": {
            "duration": 0.0,
            "outcome": "failed",
            "longrepr": longrepr # Collector error
        },
    }

async def run_instance(
    client: aiodocker.Docker,
    repo: str,
    instance_id: str,
    base_commit: str,
    patches: list[str],
    tests: list[str],
    root_log_dir: str,
    spec_dict: dict,
    timeout: int=300,
    verbose: bool=False,
    short: bool=True,
    skipped_ok: bool=True,
    host_config: dict=None,
    ignore_collector_errors: bool=False,
    failfast: bool=False,
    green_zone: bool=False,
    registry_config: DockerRegisteryConfig=GLOBAL_REGISTRY_CONFIG,
    pipeline: EvaluationPipelineInterface=DEFAULT_PIPELINE,
) -> dict:
    """Run a single instance test.

    Args:
        client: The aiodocker client.
        repo: The repository name.
        instance_id: The instance ID.
        base_commit: The base commit to test.
        patches: The list of patches to apply.
        tests: The list of tests to run.
        root_log_dir: The log directory to save the logs.
        spec_dict: The spec dictionary.
        timeout: The timeout for the test.
        verbose: Whether to print the stdout.
        short: Whether to return a short version of the results.
        skipped_ok: Whether to consider skipped tests as PASSED. (Only if short is True)
        host_config: The host configuration for the container.
        ignore_collector_errors: Set test results to failed if there are errors in collecting tests. EXCEPT ImportError!
        failfast: Whether to stop on the first failure.
        green_zone: Is the evaluate environment under the Green zone.
        registry_config: The Docker registry configuration.
        pipeline: The evaluation pipeline interface.

    Returns:
        The test results.
    """

    image_name = await build_docker_image_from_specs(
        client,
        repo,
        spec_dict,
        os.path.join(root_log_dir, 'build_logs'),
        force_rebuild=False,
        green_zone=green_zone,
        registry_config=registry_config,
    )

    # Create log directories
    log_dir = os.path.join(root_log_dir, 'evaluate_logs', instance_id)
    docker_work_dir = os.path.join(log_dir, "docker")
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(docker_work_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "run_instance.log")

    with MindForgeHarnessLogger(instance_id, log_file, add_stdout=verbose) as logger:
        logger.info(f"Running instance {instance_id} for {repo} with commit {base_commit}")

        # Prepare volume mounts for patches
        volumes = []
        for i, patch in enumerate(patches):
            patch_file = Path(docker_work_dir) / f"patch_{i}.patch"
            patch_file.write_text(patch)
            volumes.append(f"{patch_file.resolve()}:/patches/patch_{i}.patch:ro")

        # Results folder
        abs_log_dir = Path(log_dir).resolve()
        os.makedirs(os.path.join(abs_log_dir, "results"), exist_ok=True)
        volumes.append(f"{abs_log_dir}/results:/results:rw")

        # The test report
        Path(abs_log_dir, "results/pytest_report.json").write_text("")
        volumes.append(f"{abs_log_dir}/results/pytest_report.json:/pass_report.json:rw")

        # Format the entrypoint shell script
        if not tests:
            logger.warning(f"There is no test in {instance_id}. Is this expected?")
        formatted_entry = pipeline.format_eval_script(
            tests=tests,
            test_cmd=spec_dict["test_cmd"],
            eval_commands=spec_dict.get("eval_commands", []),
            install=spec_dict.get("install", ""),
            timeout=min(int(timeout / 2), UNITEST_TIMEOUT_MAX), # Give some time for the installation and setup and 3 seconds for the uni-test maximum
            pyversion = f"python{'.'.join(spec_dict['python'].replace('python','').split('.')[:2])}", # Agent typically includes the patch in python version (e.g., python x.y.z)  
            failfast=failfast,
        )
        eval_file = Path(docker_work_dir) / "eval.sh"
        eval_file.write_text(formatted_entry)
        volumes.append(f"{eval_file.resolve()}:/eval.sh:rw")

        container_config = {
            "Image": image_name,
            "HostConfig": {
                "Privileged": False,
                "Binds": volumes
            },
            "Cmd": ["sh", "-c", "chmod +x /eval.sh && /eval.sh"],
            "Env": [
                f"GIT_COMMIT={base_commit}",
                f"REPO={repo}",
                f"INSTANCE_ID={instance_id}"
            ],
            "Tty": True,
        }
        if host_config:
            container_config["HostConfig"].update(host_config)

        eval_start = time.perf_counter()
        container_name = f"{image_name.replace('/', '-').replace(':', '-')}-{uuid4()}"
        container = await client.containers.create_or_replace(name=container_name, config=container_config)
        await container.start()

        try:
            # Wait for container completion or timeout
            try:
                await asyncio.wait_for(container.wait(), timeout=timeout)
            except TimeoutError:
                await container.kill()
                # Fetch logs
                logs = await container.log(stdout=True, stderr=True)
                logger.debug("\n".join(logs))
                error = f"Container timed out after {timeout} seconds."
                logger.error(error)
                raise TimeoutError(error)

            # Fetch logs
            logs = await container.log(stdout=True, stderr=True)
            logger.info(f"Container {instance_id} exited in {time.perf_counter() - eval_start:.2f} seconds.")
            logger.debug("\n".join(logs))

            return pipeline.gather_results(abs_log_dir, logger, tests, skipped_ok, short, ignore_collector_errors)

        except KeyboardInterrupt as e:
            logger.warning("KeyboardInterrupt: Stopping the container...")
            await container.delete(force=True)
            raise e
        finally:
            # Cleanup container
            try:
                await container.delete(force=True)
            except Exception as e:
                logger.error(f"Failed to delete container: {e}")
