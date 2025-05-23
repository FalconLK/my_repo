# MindForge Harness

An autonomous harness system to produce high-quality SE-LLM training data collection at scale.

## Description

MindForge Harness is a tool for evaluating and producing high-quality software engineering datasets. It provides a framework for running tests in isolated Docker environments, ensuring reproducibility and reliability of evaluations.

## Installation

To install MindForge Harness, clone this repository and install it as a development package:

```bash
pip install -e .
```

## Requirements

- Docker
- Dependencies listed in the `pyproject.toml` file

## Online Running Evaluations

To run evaluations using the online API server:

1. First, start the server:
```bash
python -m mindforge_harness.server \
    --dataset_name path/to/dataset.jsonl \
    --max_workers 10 \
    --port 9400
```

2. Then you can use the API endpoints to run evaluations:

- Run a single instance:
```python
import requests

response = requests.get(
    "http://localhost:9400/run_one_instance",
    json={
        "instance_id": "instance_1",
        "model_patch": "your_patch_here"
    }
)
```

- Run multiple instances in parallel:
```python
import requests

instances = {
    "instance_1": "patch_1",
    "instance_2": "patch_2"
}
response = requests.get(
    "http://localhost:9400/run_many_instances",
    json=instances
)
```

### Options
The server supports the following command-line arguments:
- `--host`: Host to bind the server to (default: "0.0.0.0")
- `--port`: Port to run the server on (default: 9400)
- `--dataset_name`: Path to the dataset file (required)
- `--max_workers`: Maximum number of workers for parallel processing (default: 10)
- `--use_tmp_dir`: Use a temporary directory for the evaluation (default: False)
- `--green_zone`: Use the green zone for the evaluation (default: False)
- `--timeout`: Timeout for the evaluation in seconds (default: 300)

### Quick run
```bash
python -m mindforge_harness.server \
    --dataset_name ./tests/test_data/five_instances.jsonl 
```
```bash
python test_client.py
```

## Offline Running Evaluations

To evaluate a dataset using the gold standard predictions:

```bash
python -m mindforge_harness.main \
    --predictions_path gold \
    --max_workers 5 \
    --mode evaluate \
    --dataset_name tests/test_data/five_instances.jsonl
```

### Command-line Arguments

- `--dataset_name`: Path to the dataset file (required)
- `--mode`: Mode of operation: 'produce' or 'evaluate' (required)
- `--output_path`: Path to the output directory (default: "")
- `--max_workers`: Maximum number of workers for parallel processing (default: 1)
- `--run_id`: Run ID for the current execution (auto-generated if not provided)
- `--instance_ids`: Space-separated list of instance IDs to run (all instances if not provided)
- `--black_list`: Specifying the black list that causes broken test
- `--predictions_path`: Path to the predictions file; use "gold" for gold standard predictions
- `--output_passed`: Whether to output passed instances (default: True)
- `--timeout`: Instance timeout in seconds (default: 300)
- `--green_zone`: Add Huawei Greenzone certificates (flag)
- `--failfast`: Stop evaluation on the first failure (flag)
- `--use_tmp_dir`: Use a temporary directory for the log path (flag)

### Docker Registry Options (Optional)

- `--push_to_registry`: Push the image to the specified registry after a successful build
- `--pull_from_registry`: Check the registry when images are not found locally
- `--registry_url`: URL of the Docker registry
- `--registry_user`: Username to authenticate to the registry
- `--registry_pass`: Password to authenticate to the registry

### Environment Variables (Optional)

The following environment variables can be used to configure the behavior of MindForge Harness:

- `GIT_REPO_CACHE_DIR`: Directory to cache cloned Git repositories (default: "git_repo_caches")
- `MF_PUSH_TO_REGISTRY`: Set to "true" or "1" to enable pushing images to Docker registry (default: "false")
- `MF_PULL_FROM_REGISTRY`: Set to "true" or "1" to enable pulling images from Docker registry (default: "false")
- `MF_REGISTRY_URL`: URL of the Docker registry to use
- `MF_REGISTRY_USER`: Username for Docker registry authentication
- `MF_REGISTRY_PASS`: Password for Docker registry authentication

## Examples

### Basic Evaluation

```bash
python -m mindforge_harness.main \
    --predictions_path gold \
    --max_workers 4 \
    --mode evaluate \
    --dataset_name path/to/dataset.json
```

### Running with Specific Instance IDs

```bash
python -m mindforge_harness.main \
    --predictions_path gold \
    --max_workers 1 \
    --mode evaluate \
    --dataset_name total.json \
    --instance_ids "instance_1 instance_2 instance_3"
```

Please refer to `README_developer.md` for detailed information about contributing to this project.
