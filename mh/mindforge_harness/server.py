import argparse
import logging
import asyncio
import os
import time
import tempfile
import traceback
import shutil
import aiodocker
import uvicorn
from fastapi import FastAPI
from mindforge_harness.utils import load_dataset_from_path, prepare_dataset_for_evaluation
from mindforge_harness.run_instance import run_instance

logger = logging.getLogger(__name__)

app = FastAPI()

parser = argparse.ArgumentParser()

parser.add_argument("--host", type=str, default="0.0.0.0")

parser.add_argument("--port", type=int, default=9400)

parser.add_argument("--dataset_name", type=str, help="Path to the dataset file.")

parser.add_argument("--max_workers", type=int, default=10, help="Maximum number of workers to use for parallel processing.") 

parser.add_argument("--use_tmp_dir", type=bool, default=False, help="Use a temporary directory for the evaluation.")

parser.add_argument("--green_zone", action="store_true", default=False, help="Use the green zone for the evaluation.")

parser.add_argument("--timeout", type=int, default=300, help="Timeout for the evaluation.")

args = parser.parse_args()

dataset = load_dataset_from_path(args.dataset_name)
dataset = {
    x["instance_id"]: x
    for x in dataset
}

sem = asyncio.Semaphore(args.max_workers)

docker_client = None

if args.use_tmp_dir:
    log_dir = tempfile.TemporaryDirectory()
else:
    log_dir = "logs"

async def run_on_instance(instance_id: str, model_patch: str):
    global docker_client
    if not docker_client:
        docker_client = aiodocker.Docker()
    async with sem:
        try:
            start_time = time.perf_counter()
            instance_log_dir = os.path.join(log_dir, instance_id)
            os.makedirs(instance_log_dir, exist_ok=True)
            instance_args = dataset[instance_id]
            results = await run_instance(
                client=docker_client,
                repo=instance_args["repo"],
                instance_id=instance_args["instance_id"],
                base_commit=instance_args["base_commit"],
                patches=[model_patch,instance_args["test_patch"]],
                spec_dict=instance_args['spec_dict'],
                tests=instance_args["FAIL_TO_PASS"] + instance_args["PASS_TO_PASS"],
                root_log_dir=instance_log_dir,
                timeout=args.timeout,
                verbose=False,
                short=True,
                skipped_ok=True,
                host_config= {"NetworkMode": "none", "NanoCpus": 2000000000, "Memory": 2147483648},
                green_zone=args.green_zone
            )
            resolved = all([code for code in results.values()])
            time_elapsed = time.perf_counter() - start_time
            logger.info(f"Resolved {instance_id} in {time_elapsed:.2f} seconds. Resolved: {resolved}")
            return {
                "instance_id": instance_id,
                "resolved": resolved,
                "time": time_elapsed
            }
        except Exception as e:
            logger.error(f"Error running {instance_id}: {e}")
            logger.error(traceback.format_exc())
            return {
                "instance_id": instance_id,
                "resolved": False,
                "time": time.perf_counter() - start_time,
                "error": str(e)
            }
        finally:
            if os.path.exists(instance_log_dir):
                shutil.rmtree(instance_log_dir, ignore_errors=True)

@app.get("/run_one_instance")
async def run_one_instance(json_payload: dict):
    return await run_on_instance(json_payload["instance_id"], json_payload["model_patch"]   )

@app.get("/run_many_instances")
async def run_many_instances(json_payload: dict):
    return await asyncio.gather(*[
        run_on_instance(instance_id, model_patch)
        for instance_id, model_patch in json_payload.items()
    ])
    

if __name__ == "__main__":
    uvicorn.run(app, host=args.host, port=args.port)