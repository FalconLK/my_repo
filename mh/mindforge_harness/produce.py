"""Evaluation module for the MindForge harness."""
import logging
import os
import time

import orjson

from mindforge_harness.docker.image_builder import get_image_name
from mindforge_harness.evaluate import evaluate
from mindforge_harness.logger import MindForgeHarnessLogger
from mindforge_harness.utils import (
    extract_crash_details_from_report,
    extract_modified_test_files,
    load_dataset_from_path,
    prepare_dataset_for_evaluation,
)

PASS_OUTCOMES = ('passed', 'skipped')
FAIL_OUTCOMES = ('failed', 'error')

def save_results(run_id: str, raw_dataset: list, instance_id2results: dict, output_path: str, logger: logging.Logger, f2p_data_only: bool=False):
    """Save evaluation results to a JSON file.
    
    Args:
        raw_dataset (list): The raw dataset used for evaluation.
        instance_id2results (dict): A dictionary mapping instance IDs to their respective results.
        output_path (str): The directory path where the results will be saved.
        logger (logging.Logger): Logger instance for logging information.
        f2p_data_only (bool, optional): Flag to indicate if only f2p (feature to predict) data should be saved. Defaults to False.
    """
    output = orjson.dumps(instance_id2results, option=orjson.OPT_INDENT_2) 
    
    if run_id:
        produce_report_path = os.path.join(output_path, "produce_report-%s.json" % run_id)
    else:
        produce_report_path = os.path.join(output_path, "produce_report.json")
    with open(produce_report_path, "wb") as f:
        f.write(output)
    logger.info(f"Produce report saved to {produce_report_path}")
    
    if run_id:
        produced_dataset_path = os.path.join(output_path, "produced_dataset-%s.jsonl" % run_id)
    else:
        produced_dataset_path = os.path.join(output_path, "produced_dataset.jsonl")
    num_writen = 0
    with open(produced_dataset_path, "wb") as f:
        for line in raw_dataset:
            if line['instance_id'] not in instance_id2results:
                continue
            fail_to_pass = instance_id2results[line['instance_id']].get('f2p', [])
            pass_to_pass = instance_id2results[line['instance_id']].get('p2p', [])
            fail_to_fail = instance_id2results[line['instance_id']].get('f2f', [])          
            
            
            if line.get("spec_dict") is not None:
                image_name = get_image_name(repo_name=line['repo'], spec_dict=line['spec_dict'])
            else:
                image_name = ""
                
            line = {
                **line,
                "PASS_TO_PASS": pass_to_pass,
                "FAIL_TO_PASS": fail_to_pass,
                "FAIL_TO_FAIL": fail_to_fail,
                "task_score": line.get("task_score", -1),
                "evaluation_score": line.get("evaluation_score", -1),
                "difficulty_score": line.get("difficulty_score", -1),
                "image": image_name, 
            }
            if f2p_data_only:
                if instance_id2results[line['instance_id']].get('f2p', []):
                    num_writen += 1
                    f.write(orjson.dumps(line) + b'\n')
            else:
                num_writen += 1
                f.write(orjson.dumps(line) + b'\n')
    logger.info(f"Produced {num_writen} instances saved to {produced_dataset_path}")

def gather_results(pre_golden_round_results: list, golden_round_results: list) -> dict:
    """Gather the results of the pre-golden and golden rounds."""
    instance_id2results={}
    for instance_id, golden_round_result in golden_round_results.items():
        if instance_id not in pre_golden_round_results:
            continue
        pre_golden_round_result = pre_golden_round_results[instance_id]

        # Handle case where golden round encountered an error
        if 'error' in golden_round_result:
            instance_id2results[instance_id] = {'error': golden_round_result['error']}
            continue

                
        result_entry = {
            'p2p': [],
            'f2p': [],
            'f2f': [],
            'p2f': [],
            'f2f_test_details': {},
            'p2f_test_details': {}
        }

        # Extract second round test outcomes (True if passed/skipped)
        golden_tests_passed = {
            test['nodeid']
            for test in golden_round_result.get('tests', [])
            if test['outcome'] in PASS_OUTCOMES
        }
        # Collect crash details once
        try:
            crash_details = extract_crash_details_from_report(golden_round_result)
        except Exception as e:
            print(repr(e))

        golden_pass = golden_tests_passed
        golden_fail = set(crash_details.keys())

        if 'error' in pre_golden_round_result:
            # First round had errors, second round did not
            result_entry['f2p'] = list(golden_tests_passed)
            result_entry['f2f'] = list(crash_details.keys())
            result_entry['f2f_test_details'] = crash_details
        else:

            pre_golden_pass = {test['nodeid'] for test in pre_golden_round_result.get('tests', []) if test['outcome'] in PASS_OUTCOMES}
            pre_golden_fail = {test['nodeid'] for test in pre_golden_round_result.get('tests', []) if test['outcome'] in FAIL_OUTCOMES}

            result_entry.update({
                'f2p': list(pre_golden_fail & golden_pass),
                'p2p': list(pre_golden_pass & golden_pass),
                'f2f': list(pre_golden_fail & golden_fail),
                'p2f': list(pre_golden_pass & golden_fail),
                'f2f_test_details': {test: crash_details[test] for test in pre_golden_fail & golden_fail},
                'p2f_test_details': {test: crash_details[test] for test in pre_golden_pass & golden_fail},
            })
        # Some tests folder doesn't contain an __init__
        # in this case, use the abs path of the test
        root_dir = golden_round_result.get("root")
        if root_dir and root_dir != '/workspace':
            for e in ['f2p','p2p', 'f2f', 'p2f']:
                result_entry[e] = [os.path.join(root_dir, test) for test in result_entry[e]]
            for e in ['f2f_test_details', 'p2f_test_details']:
                result_entry[e] = {
                    os.path.join(root_dir, k): v for k, v in result_entry[e].items()
                }
        instance_id2results[instance_id] = result_entry

    return instance_id2results

async def run_produce(
    dataset_name: str,
    max_workers: int,
    run_id: str,
    output_path: str,
    instance_ids: str,
    output_passed: bool,
    timeout: int=300,
    black_list: str=None,
    green_zone: bool=False,
    spec_dict: dict=None,
    batch_mode=True,
    ):
    """Run the evaluation."""
    with MindForgeHarnessLogger("produce-top", log_file=None, add_stdout=True) as logger:
        logger.info(f"Run ID: {run_id}")
        start_time = time.perf_counter()
        # Load the dataset locally or using huggingface datasets
        if dataset_name.endswith(".json") or dataset_name.endswith(".jsonl"):
            raw_dataset = load_dataset_from_path(dataset_name)

        # Load spec_dict if provided
        if spec_dict:
            if 'pip_packages' in spec_dict: # A non-versioned spec_dict
                for instance_data in raw_dataset:
                    instance_data['spec_dict'] = spec_dict
            else: # A versioned spec_dict
                for instance_data in raw_dataset:
                    instance_data["spec_dict"] = spec_dict.get(instance_data.get("version",  "default"))
        
        dataset = prepare_dataset_for_evaluation(
            raw_dataset,
            instance_ids.split() if instance_ids else None
        )
        for _, instance_data in dataset.items():
            test_patch = instance_data['patches'][0]
            instance_data["tests"] = extract_modified_test_files(test_patch, black_list)
            instance_data['spec_dict']['test_cmd'] = instance_data['spec_dict']['test_cmd'] + " --continue-on-collection-errors"

        logger.info("Run produce golden round")
        log_dir = os.path.join("logs", f"produce-golden-eval-{run_id}" if run_id else f"produce-golden-eval-{time.strftime('%Y%m%d-%H%M%S')}")
        golden_round_results = await evaluate(log_dir, dataset, max_workers, timeout=timeout, ignore_collector_errors=True, green_zone=green_zone, batch_mode=batch_mode, short=False)
        
        for _, instance_data in dataset.items():
            instance_data['patches'] = [instance_data['patches'][0]]

        log_dir = os.path.join("logs", f"produce-pre-golden-eval-{run_id}" if run_id else f"produce-pre-golden-eval-{time.strftime('%Y%m%d-%H%M%S')}")
        
        logger.info("Run produce pre-golden round")
        pre_golden_round_results = await evaluate(log_dir, dataset, max_workers, timeout=timeout, ignore_collector_errors=True, green_zone=green_zone, batch_mode=batch_mode, short=False)

        instance_id2results = gather_results(pre_golden_round_results, golden_round_results)
    
        logger.info(f"Completed produce in {time.perf_counter() - start_time:.2f} seconds.")
        logger.info(f"Total instances: {len(instance_id2results)}")

        os.makedirs(output_path, exist_ok=True)
        
        if output_passed:
            save_results(run_id, raw_dataset, instance_id2results, output_path, logger, f2p_data_only=True)
            if output_path:
                logger.info(f"Filtered data saved to {output_path}")
            else:
                logger.info("Filtered data saved to current directory")

            
        return instance_id2results
