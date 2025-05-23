"""Run the evaluation script for the Mindforge Harness system."""
import argparse
import asyncio
import json

from mindforge_harness.evaluate import run_evaluate
from mindforge_harness.produce import run_produce
from mindforge_harness.docker.docker_utils import GLOBAL_REGISTRY_CONFIG

parser = argparse.ArgumentParser(description="An autonomous harness system to produce high-quality SE-LLM training data collection at scale.")

parser.add_argument("--dataset_name", type=str, help="Path to the dataset file.")

parser.add_argument("--mode", type=str, help="Mode of operation: 'produce' or 'evaluate'.")

parser.add_argument("--output_path", type=str, default="", help="Path to the output directory.")

parser.add_argument("--max_workers", type=int, default=1, help="Maximum number of workers to use for parallel processing.")

parser.add_argument("--run_id", type=str, default="", help="Run ID for the current execution. Automatically generated if not provided.")

parser.add_argument("--instance_ids", type=str, default="", help="Space-separated list of instance IDs to run. Run all instances if not provided.")

parser.add_argument("--black_list", type=str, default="", help="Specifying the black list that causes broken test, using all the resources.")

parser.add_argument('--predictions_path', type=str, default='gold', help="Path to the predictions file. gold for gold standard predictions.")

parser.add_argument("--output_passed", action='store_true', default=False, help="Output the passed instances.")

parser.add_argument("--timeout", type=int, default=300, help="Instance time out.")

parser.add_argument("--green_zone", action='store_true', help='Add Huawei Greenzone certificates.')

parser.add_argument("--spec_dict", type=str, default=None, help="Specification dictionary for the evaluation.")

parser.add_argument("--push_to_registry", action='store_true', default=False, help="After a successful image build, whether to push the image to the specified registry_url.")

parser.add_argument("--pull_from_registry", action='store_true', default=False, help="Whether evaluation time should check the registry when images are not found locally. Set to True to speed up evaluation and skip build when images are available in registry_url. Set to False to ensure images are always built and pulled from local machine for debugging/ testing.")

parser.add_argument("--registry_url", type=str, default=None, help="<ip:port> or <hostname:port> of the Docker registry that will store the pushed images, and also where the harness will check for pre-built images at evaluation time.")

parser.add_argument("--registry_user", type=str, default=None, help="Username to authenticate to the registry.")

parser.add_argument("--registry_pass", type=str, default=None, help="Password to authenticate to the registry.")

parser.add_argument("--batch_mode", action='store_true', default=False, help="Whether to run in batch mode or not.")

parser.add_argument("--failfast", action='store_true', default=False, help="Whether to stop the evaluation on the first failure.")

parser.add_argument("--use_tmp_dir", action='store_true', default=False, help="Whether to use a temporary directory for the output path.")

def main(mode: str, spec_dict: str = None, **kwargs):
    """Run the main function for the evaluation script."""
    if spec_dict:
        kwargs['spec_dict'] = json.loads(spec_dict)

    GLOBAL_REGISTRY_CONFIG['push_to_registry'] = kwargs.pop("push_to_registry") or GLOBAL_REGISTRY_CONFIG['push_to_registry']
    GLOBAL_REGISTRY_CONFIG['pull_from_registry'] = kwargs.pop("pull_from_registry") or GLOBAL_REGISTRY_CONFIG['pull_from_registry']
    GLOBAL_REGISTRY_CONFIG['registry_url'] = kwargs.pop("registry_url") or GLOBAL_REGISTRY_CONFIG['registry_url']
    GLOBAL_REGISTRY_CONFIG['registry_user'] = kwargs.pop("registry_user") or GLOBAL_REGISTRY_CONFIG['registry_user']
    GLOBAL_REGISTRY_CONFIG['registry_pass'] = kwargs.pop("registry_pass") or GLOBAL_REGISTRY_CONFIG['registry_pass']

    if mode == "produce":
        asyncio.run(run_produce(
            dataset_name=kwargs.pop("dataset_name"),
            max_workers=kwargs.pop("max_workers"),
            run_id=kwargs.pop("run_id"),
            output_path=kwargs.pop("output_path"),
            instance_ids=kwargs.pop("instance_ids"),
            output_passed=kwargs.pop("output_passed"),
            spec_dict=kwargs.get("spec_dict"),
            timeout=kwargs.pop("timeout"),
            black_list=kwargs.pop("black_list"),
            batch_mode=kwargs.pop("batch_mode"),
            green_zone=kwargs.pop("green_zone"),
        ))
    elif mode == "evaluate":
        run_evaluate(
            dataset_name=kwargs.pop("dataset_name"),
            max_workers=kwargs.pop("max_workers"),
            run_id=kwargs.pop("run_id"),
            output_path=kwargs.pop("output_path"),
            instance_ids=kwargs.pop("instance_ids"),
            predictions_path=kwargs.get("predictions_path"),
            output_passed=kwargs.pop("output_passed"),
            timeout=kwargs.pop("timeout"),
            failfast=kwargs.pop("failfast"),
            green_zone=kwargs.pop("green_zone"),
            use_tmp_dir=kwargs.pop("use_tmp_dir"),
        )
    else:
        raise ValueError(f"Invalid mode: {mode}")
    

if __name__ == '__main__':
    args = parser.parse_args()
    main(**vars(args))
