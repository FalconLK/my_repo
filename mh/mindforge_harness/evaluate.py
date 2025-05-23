"""Evaluation module for the MindForge harness."""
import asyncio
import os
import tempfile
import time
import traceback
from pathlib import Path

import aiodocker
import orjson
from aiohttp import ClientSession, ClientTimeout, UnixConnector
from tqdm.asyncio import tqdm

from mindforge_harness.docker.image_builder import (
    build_docker_image_from_specs,
    GLOBAL_REGISTRY_CONFIG,
)
from mindforge_harness.run_instance import EvaluationPipelineInterface, run_instance, DEFAULT_PIPELINE
from mindforge_harness.logger import MindForgeHarnessLogger, TQDMLogger
from mindforge_harness.utils import (
    load_dataset_from_path,
    prepare_dataset_for_evaluation,
)

async def evaluate(
    log_dir: str,
    dataset: dict[str, dict],
    max_workers: int,
    timeout: int=300,
    ignore_collector_errors: bool=False,
    green_zone: bool=False,
    no_network: bool=True,
    batch_mode: bool=False,
    short: bool=True,
    failfast: bool=False,
    pipeline: EvaluationPipelineInterface=DEFAULT_PIPELINE
) -> dict[str, dict]:
    """Evaluate the dataset."""
    with TQDMLogger("evaluate", os.path.join(log_dir, "evaluation.log")) as logger:
        
        logger.info(f"Logs saved to {os.path.join(log_dir, 'evaluation.log')}")
        socket_path = '/var/run/docker.sock'
        if os.path.exists(socket_path) and Path(socket_path).is_socket():
            client = aiodocker.Docker(
                session=ClientSession(
                    connector=UnixConnector(socket_path),
                    timeout=ClientTimeout(total=timeout, sock_connect=30) # sock_connect=30 is the default value
                )
            )
        else:
            logger.debug("Timeout for building is not applied.")
            client = aiodocker.Docker()
        async with client:
            sem = asyncio.Semaphore(max_workers)
            queue = asyncio.Queue()
            results = {}
            
            instance_datas = list(dataset.values())
                    
            with tqdm(total=len(dataset), desc="Evaluating", dynamic_ncols=True) as pbar:
                
                async def evaluate_worker():
                    """Worker function to evaluate the instances."""
                    while True:
                        instance_args = await queue.get()
                        try:
                            if instance_args is None: # Sentinel value to break the loop
                                break
                            async with sem: # Controls the concurrency
                                if not instance_args['tests']:
                                    logger.warning(f"There is no test in {instance_args['instance_id']}. Is this expected?")

                                await build_docker_image_from_specs(
                                    client=client,
                                    repo_name=instance_args["repo"],
                                    spec_dict=instance_args.get("spec_dict", None),
                                    docker_work_dir=os.path.join(log_dir, "build_logs"),
                                    force_rebuild=False,
                                    green_zone=green_zone,
                                    registry_config=GLOBAL_REGISTRY_CONFIG,
                                )
                                start_time = time.perf_counter()
                                assert instance_args.get("spec_dict"), "The function 'get_spec_from_hardcode()' is deprecated and removed in future versions." \
                                    "Please specify your specs directly in the dataset using the 'spec_dict' entry."
                                result = await run_instance(
                                    client=client,
                                    repo=instance_args["repo"],
                                    instance_id=instance_args["instance_id"],
                                    base_commit=instance_args["base_commit"],
                                    patches=instance_args["patches"],
                                    tests=instance_args["tests"],
                                    root_log_dir=log_dir,
                                    spec_dict=instance_args["spec_dict"],
                                    timeout=instance_args.get("timeout") or timeout,
                                    verbose=False,
                                    short=short,
                                    skipped_ok=True,
                                    host_config={"NetworkMode": "none"} if no_network else None,
                                    ignore_collector_errors=ignore_collector_errors,
                                    failfast=failfast,
                                    green_zone=green_zone,
                                    pipeline=pipeline
                                )

                                if short:
                                    logger.info(f"Evaluated instance {instance_args['instance_id']} in {time.perf_counter() - start_time:.2f} seconds. \
                                    Resolved: {all([code for code in result.values()])}")
                                    results[instance_args["instance_id"]] = {
                                        "tests": result,
                                        "time": time.perf_counter() - start_time,
                                    }
                                else:
                                    logger.info(f"Evaluated instance {instance_args['instance_id']} in {time.perf_counter() - start_time:.2f} seconds.")
                                    results[instance_args["instance_id"]] = result
                        except Exception as e:
                            tb = traceback.format_exc()
                            logger.debug(tb)
                            logger.debug(f"Error evaluating instance {instance_args['instance_id']}: {e}")
                            logger.info(f"Evaluated instance {instance_args['instance_id']}. Resolved: {f'Timeout after {timeout} seconds.' if isinstance(e, TimeoutError) else 'Error'}")
                            results[instance_args["instance_id"]] = {"error": str(e), "traceback": tb}
                        finally:
                            pbar.update(1)
                            queue.task_done()  

                workers = [asyncio.create_task(evaluate_worker()) for _ in range(max_workers)]
                
                if not batch_mode:
                    for instance_data in instance_datas:
                        await queue.put(instance_data)

                    await queue.join()
                else:
                    logger.info("Batch mode is enabled.")
                    # Batch mode
                    for i in range(0, len(instance_datas), max_workers):
                        batch = instance_datas[i:i+max_workers]
                        instance_ids = [instance_data['instance_id'] for instance_data in batch]
                        
                        await asyncio.gather(*(queue.put(data) for data in batch))
                        await queue.join()
                        
                        # If all instances in the branch are error, then stop the evaluation
                        if all("error" in results[iid] for iid in instance_ids):
                            logger.info("All instances in the batch are errors. Stopping evaluation.")
                            break
                
                # Release the workers
                for _ in range(max_workers):
                    await queue.put(None)
            
                await asyncio.gather(*workers, return_exceptions=True)
            
                return results

def run_evaluate(
    dataset_name: str,
    max_workers: int,
    run_id: str,
    output_path: str,
    instance_ids: str,
    predictions_path: str,
    output_passed: bool,
    timeout: int=3000,
    failfast: bool=False,
    green_zone: bool=False,
    use_tmp_dir: bool=False,
    ):
    """Run the evaluation."""
    with MindForgeHarnessLogger("evaluate-top", log_file=None, add_stdout=True) as logger:
        logger.info(f"Run ID: {run_id}")
        if green_zone:
            logger.info("Running in Green Zone. Using Huawei Certificates.")
        start_time = time.perf_counter()
        # Load the dataset locally or using huggingface datasets
        if dataset_name.endswith(".json") or dataset_name.endswith(".jsonl"):
            raw_dataset = load_dataset_from_path(dataset_name)
        else:
            raise ValueError(f"Dataset file format not supported: {dataset_name}")
        
        dataset = prepare_dataset_for_evaluation(
            raw_dataset,
            instance_ids.split() if instance_ids else None,
            predictions_path if predictions_path != "gold" else None
        )
        run_id = run_id or f"evaluate-{time.strftime('%Y%m%d-%H%M%S')}"
        temp_dir = None
        try:
            if use_tmp_dir:
                # Get a temporary directory
                temp_dir = tempfile.TemporaryDirectory()
                log_dir = os.path.join(temp_dir.name, run_id)
                os.makedirs(log_dir, exist_ok=True)
                logger.info(f"Temporary directory created at {log_dir}")
            else:
                log_dir = os.path.join("logs", run_id)
            output_path = output_path or log_dir
            
            results = asyncio.run(evaluate(
                log_dir=log_dir, 
                dataset=dataset, 
                max_workers=max_workers, 
                timeout=timeout, 
                ignore_collector_errors=False, 
                failfast=failfast,
                green_zone=green_zone,
            ))
        finally:
            if use_tmp_dir:
                # Remove the temporary directory
                temp_dir.cleanup()
                logger.info(f"Temporary directory {log_dir} removed.")

        json_output = {
            'run_id': run_id,
            'resolved': 0,
            'unresolved': 0,
            'errors': 0,
            'total': len(results),
            'resolved_instances': {},
            'unresolved_instances': {},
            'errors_instances': []
        }
        # Compose a orjson report
        for iid, result in results.items():
            if "error" in result:
                json_output['errors'] += 1
                json_output['errors_instances'].append(iid)
            elif all([code for code in result['tests'].values()]):
                json_output['resolved'] += 1
                json_output['resolved_instances'][iid] = result['time']
            else:
                json_output['unresolved'] += 1
                json_output['unresolved_instances'][iid] = result['time']
        
        output = orjson.dumps(json_output, option=orjson.OPT_INDENT_2) 
        
        logger.info(f"Completed evaluation in {time.perf_counter() - start_time:.2f} seconds.")
        logger.info(f"Total instances: {len(results)}")
        logger.info(f"Instances resolved: {json_output['resolved']}")
        logger.info(f"Instances unresolved: {json_output['unresolved']}")
        logger.info(f"Errors: {json_output['errors']}")
        
        if use_tmp_dir:  # If the temporary directory is used, return the results
            return results

        if not os.path.exists(output_path):
            os.makedirs(output_path, exist_ok=True)
        eval_report_path = os.path.join(output_path, "evaluation_report.json")
        with open(eval_report_path, "wb") as f:
            f.write(output)
        logger.info(f"Saved evaluation report to {eval_report_path}")
            
        if output_passed:
            resolved_dataset_path = os.path.join(output_path, "resolved_dataset.jsonl")
            with open(resolved_dataset_path, "wb") as f:
                for line in raw_dataset:
                    if line['instance_id'] in json_output['resolved_instances']:
                        f.write(orjson.dumps(line)+b'\n')
            logger.info(f"Saved resolved instances to {resolved_dataset_path}")
            
    return results
