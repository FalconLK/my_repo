"""Tests for evaluations."""
import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from mindforge_harness.docker_utils import build_all_images, get_image_name
from mindforge_harness.evaluate import evaluate
from mindforge_harness.utils import prepare_dataset_for_evaluation

from . import get_test_data_path


def test_resolved_instances_match_total():
    """Test if the reoslved instances match expected."""
    # Get dynamic dataset path
    dataset_path = get_test_data_path("eval_test_data.jsonl")
    
    # Define command with dynamic path and instance IDs
    command = [
        "python",
        "-m",
        "mindforge_harness.run_evaluation",
        "--dataset_name", dataset_path,
        "--predictions_path", "gold",
        "--mode", "evaluate",
        "--max_workers", "15",
        "--run_id", "sqlglot",
    ]
    
    # Execute command
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )
    
    # Verify command success
    assert result.returncode == 0, f"Command failed with error: {result.stderr}"
    
    # Parse output
    output = result.stdout

    total_match = re.search(r"Total instances: (\d+)", output)
    resolved_match = re.search(r"Instances resolved: (\d+)", output)
    error_match = re.search(r"Errors: (\d+)", output)
    
    # Extract values
    total_instances = int(total_match.group(1)) if total_match else 0
    resolved_instances = int(resolved_match.group(1)) if resolved_match else 0
    error_instances = int(error_match.group(1)) if error_match else 0
    
    # Assert resolution match
    assert total_instances == total_instances, "Resolved instances do not match total instances"
    assert resolved_instances == 1, "Resolved instances do not match resolved instances" # Pydantic
    assert error_instances == 2, "Resolved instances do not match error instances" # HTTPX


@pytest.mark.skipif(not Path('/var/run/docker.sock').is_socket(), reason="Only works on Linux machine")
def test_build_timeout():
    """Test custom timeout."""
    dataset_path = get_test_data_path("one_sample.jsonl")
    with open(dataset_path) as f:
        data = [json.loads(f.readline())]

    data = prepare_dataset_for_evaluation(data)

    data['encode__httpx-2495']['spec_dict']['pre_install'] = [
        'sleep 100' # It must timeout
    ]
    log_dir = "logs/test_timeout"
    asyncio.run(evaluate(
        log_dir,
        data,
        max_workers=1,
        timeout=1,
        green_zone=False,
    ))

    with open(os.path.join(log_dir, "build_logs", get_image_name(data['encode__httpx-2495']['repo'], data['encode__httpx-2495']['spec_dict']), "build.log")) as f:
        assert "Build timeout after 1 seconds." in f.read()

def test_single_test_timeout():
    """Test custom timeout."""
    dataset_path = get_test_data_path("timeout_data.jsonl")
    asyncio.run(build_all_images(dataset_path))
    command = [
        "python",
        "-m",
        "mindforge_harness.run_evaluation",
        "--dataset_name", dataset_path,
        "--predictions_path", "gold",
        "--mode", "evaluate",
        "--max_workers", "2",
        "--run_id", "pytest",
        "--timeout", "60",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    result = result.stdout
    
    cwd = Path(os.getcwd())
    report_path = cwd / "logs" / "pytest" / "evaluation_report.json"

    # Optional: Check if the file truly exists or do other fallback logic
    # before attempting to open it.
    if not report_path.is_file():
        raise FileNotFoundError(f"Evaluation report not found at: {report_path}")

    with open(report_path, "r") as f:
        report = json.load(f)

    assert report['resolved'] == 1
    assert report['unresolved'] == 1
    assert report['errors'] == 0
    assert 'test_1' in report['resolved_instances']
    assert 'test_300' in report['unresolved_instances']