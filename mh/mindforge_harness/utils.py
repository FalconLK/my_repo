"""Utility functions for the MindForge harness."""
import hashlib
import io
import os
import posixpath
import re
import shutil
import tarfile

import git
import orjson
import requests

from mindforge_harness.docker.consts import DOCKER_IMAGE_COMBINED

# Get the current directory
GIT_REPO_CACHE_DIR = os.environ.get("GIT_REPO_CACHE_DIR", "git_repo_caches")

def is_valid_git_repo(path: str) -> bool:
    """Check if a directory is a valid Git repository."""
    try:
        _ = git.Repo(path).heads
        return True
    except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
        return False
    
def get_cached_or_clone_repo(repo_name: str, clean_cache: bool=False) -> str:
    """Get the cached or cloned repository path."""
    repo_path = os.path.join(GIT_REPO_CACHE_DIR, repo_name.replace('/', '__'))
    repo_path = os.path.abspath(repo_path)
    
    if os.path.exists(repo_path):
        if not clean_cache and os.path.isdir(repo_path) and os.listdir(repo_path):
            if is_valid_git_repo(repo_path):
                # print(f"Using cached repo at {repo_path}.")
                return repo_path
            # else:
            #     print(f"Found invalid/corrupt Git repo at {repo_path}. Will remove and re-clone.")
        
        try:
            shutil.rmtree(repo_path)
            # print(f"Removed existing repo cache at {repo_path}.")
        except Exception as ex:
            # print(f"Failed to remove cached repo path {repo_path} : {ex}")
            raise
            
    os.makedirs(repo_path, exist_ok=True)
    
    try:
        git.Repo.clone_from(f"https://github.com/{repo_name}", repo_path, recursive=True)
    except Exception as ex:
        # print(f"Clone failed : {ex}. Cleaning up.")
        try:
            shutil.rmtree(repo_path)
        except Exception as cleanup_ex:
            # print(f"Failed to remove partial clone : {cleanup_ex}")
            raise
        
    return repo_path

def extract_crash_details_from_report(report: dict) -> dict:
    """Extract crash details from the test report."""
    tests = report.get('tests', [])
    output = {}
    for test in tests:
        if test['outcome'] == 'error':
            output[test['nodeid']] = ""
            if 'setup' in test:
                output[test['nodeid']] += test['setup'].get('longrepr', "") + '\n'
            if 'call' in test:
                output[test['nodeid']] += test['call'].get('longrepr', "") + '\n'
            if 'teardown' in test:
                output[test['nodeid']] += test['teardown'].get('longrepr', "")
        elif test['outcome'] == 'failed':
            if 'crash' in test:
                output[test['nodeid']] = test['crash']
            elif 'setup' in test and 'crash' in test['setup']:
                output[test['nodeid']] = test['crash']
            elif 'call' in test and 'crash' in test['call']:
                output[test['nodeid']] = test['call']['crash']
            elif 'teardown' in test and 'crash' in test['teardown']:
                output[test['nodeid']] = test['teardown']['crash']
            else:
                output[test['nodeid']] = ""
    return output

def extract_missing_tests(stderr: str) -> list[str]:
    """Extract test names from the stderr output."""
    pattern = r"ERROR: not found: /workspace/(.+)"
    return re.findall(pattern, stderr)

def extract_modified_test_files(test_patch: str, black_list: str="") -> list[str]:
    """Extracts modified test files from a given patch, excluding deleted files."""  # noqa: D401
    modified_files = set()
    current_file = None
    is_deleted = False

    for line in test_patch.split("\n"):
        if current_file in black_list.split():
            continue
        if line.startswith('diff --git'):
            # Modified regex to handle different path structures
            match = re.match(r"diff --git a/((?:.*/)*(?:test_.*|tests_.*|.*_test|.*_tests|test|tests)\.py) b/", line)
            if match:
                current_file = match.group(1)
                is_deleted = False
            else:
                current_file = None
                is_deleted = False
        elif line.startswith('+++'):
            # Check for deletions in both a/ and b/ sides
            if line.startswith('+++ /dev/null') or line.startswith('--- /dev/null'):
                is_deleted = True
        elif line.startswith('@@'):
            # Add file if valid and not deleted
            if current_file and not is_deleted:
                modified_files.add(current_file)

    return list(modified_files)

def load_dataset_from_path(dataset_path: str) -> dict:
    """Load dataset from the given path."""
    with open(dataset_path) as f:
        if dataset_path.endswith(".json"):
            return orjson.loads(f)
        elif dataset_path.endswith("jsonl"):
            return [orjson.loads(line) for line in f]
        else:
            raise ValueError(f"Dataset file format not supported: {dataset_path}")

def prepare_dataset_for_evaluation(
    raw_dataset: dict,
    instance_ids: list[str]=None,
    prediction_path: str=None
) -> dict:
    """Prepare the dataset for evaluation."""
    dataset_dict = {data["instance_id"]: data for data in raw_dataset}

    # Filter dataset if instance_ids are provided
    if instance_ids:
        missing_ids = set(instance_ids) - dataset_dict.keys()
        if missing_ids:
            raise ValueError(f"Instance IDs not found in the dataset: {missing_ids}")
        dataset_dict = {iid: dataset_dict[iid] for iid in instance_ids}

    # Load predictions if provided
    predictions = {}
    if prediction_path:
        predictions = {data["instance_id"]: data['model_patch'] for data in load_dataset_from_path(prediction_path)}

    # Prepare dataset with necessary fields
    return {
        iid: {
            'repo': data['repo'],
            'instance_id': iid,
            'base_commit': data['base_commit'],
            'patches': [data['test_patch'], predictions.get(iid, data['patch'])],
            'tests': data['PASS_TO_PASS'] + data['FAIL_TO_PASS'],
            'spec_dict': data.get('spec_dict')
        }
        for iid, data in dataset_dict.items()
    }


def consistent_hash(data: dict, unhash_fields: list=['install', 'test_cmd', 'eval_commands']):
    """Generate a stable hash for a Python dictionary containing lists, dicts, and strings."""

    def make_hashable(obj):
        """Convert dicts and lists into a hashable, consistently ordered form."""
        if isinstance(obj, dict):
            # Convert dict to sorted tuples (key, value) for consistent ordering
            return tuple((k, make_hashable(v)) for k, v in sorted(obj.items()))
        elif isinstance(obj, list):
            # Convert list to a tuple
            return tuple(make_hashable(x) for x in obj)
        elif isinstance(obj, set):
            # Convert set to sorted tuple
            return tuple(sorted(make_hashable(x) for x in obj))
        else:
            return obj  # Strings, numbers, and other hashable types remain the same

    # Remove unhashable fields
    data = {
        k: v for k, v in data.items() if k not in unhash_fields
    }

    # Convert data into a stable format
    hashable_data = make_hashable(data)

    # Serialize using json.dumps (ensure consistent ordering)
    serialized = orjson.dumps(hashable_data, option=orjson.OPT_SORT_KEYS)

    # Compute SHA-256 hash
    combined_data = serialized + DOCKER_IMAGE_COMBINED
    return hashlib.sha256(combined_data).hexdigest()

def download_requirements_by_commit(repo_name: str, requirements_txts: list[str], commit: str) -> list[str]:
    """Get the requirements.txt file for a given commit."""
    request_url = "https://raw.githubusercontent.com/"
    headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36"
    }
    requirements = []

    def exclude_line(line: str) -> bool:
        """Exclude lines that are not requirements."""
        return any([line.strip().startswith(x) for x in ["-e .", "#", ".[test"]])

    # Recursively get requirements from all requirements.txt files
    while len(requirements_txts) > 0:
        req_file = requirements_txts.pop(0)
        req_dir = "/".join(req_file.split("/")[:-1]) # Some requirements.txt files have relative paths like ./tests/requirements.txt
        url = posixpath.join(request_url, repo_name, commit, req_file)
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            raise ValueError(f"Could not find requirements.txt at path {url}")
        lines = response.text

        for line in lines.split("\n"):
            if line.strip().startswith("-r"):
                # Handle recursive requirements
                requirements_txts.append(posixpath.join(req_dir, line[len("-r"):].strip()))
            else:
                if line and not exclude_line(line):
                    requirements.append(line)

    return requirements

def create_tarball(build_context_path: str, repo_path: str, additional_files: list[str]=None):
    """Create a tarball from the build context path, including additional files."""
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        for root, _, files in os.walk(build_context_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, build_context_path)
                tar.add(file_path, arcname=arcname)

        for root, _, files in os.walk(repo_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(repo_path))
                tar.add(file_path, arcname=arcname)

        # Add additional files if provided
        if additional_files:
            for file_path in additional_files:
                if os.path.exists(file_path):
                    arcname = os.path.basename(file_path)
                    tar.add(file_path, arcname=arcname)
                else:
                    raise FileNotFoundError(f"Additional file '{file_path}' not found.")

    tar_stream.seek(0)
    return tar_stream

