"""Tests for Productions."""
import json
import subprocess

from . import get_test_data_path


def test_produce_with_build_in_spec():
    """Test produce with a built-in spec."""
    # Get dynamic dataset path
    dataset_path = get_test_data_path("produce_test_data.jsonl")
    
    # Define command with dynamic path and instance IDs
    command = [
        "python",
        "-m",
        "mindforge_harness.run_evaluation",
        "--dataset_name", dataset_path,
        "--mode", "produce",
        "--max_workers", "20",
        "--run_id", "pytest",
        "--output_path", "logs/",
    ]
    
    # Execute command
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )
    stdout = result.stdout
    print(stdout)
    import os
    from pathlib import Path
    cwd = Path(os.getcwd())
    report_path = cwd / "logs"/ "produced_dataset.jsonl"
    assert "Produced 2 instances saved to logs/produced_dataset.jsonl" in stdout
    with open(report_path) as f:
        produced_data = [json.loads(line) for line in f]
    assert len(produced_data) == 2
    assert set(produced_data[1]['PASS_TO_PASS']) == set([ # Litestar
      "tests/test_content.py::test_response_aiterator_content",
      "tests/test_content.py::test_urlencoded_content",
      "tests/test_content.py::test_iterator_content",
      "tests/test_content.py::test_async_bytesio_content",
      "tests/test_content.py::test_urlencoded_list",
      "tests/test_content.py::test_urlencoded_boolean",
      "tests/test_content.py::test_multipart_files_content",
      "tests/test_content.py::test_bytes_content",
      "tests/test_content.py::test_empty_content",
      "tests/test_content.py::test_empty_request",
      "tests/test_content.py::test_response_iterator_content",
      "tests/test_content.py::test_json_content",
      "tests/test_content.py::test_urlencoded_none",
      "tests/test_content.py::test_aiterator_content",
      "tests/test_content.py::test_multipart_multiple_files_single_input_content",
      "tests/test_content.py::test_response_empty_content",
      "tests/test_content.py::test_response_invalid_argument",
      "tests/test_content.py::test_bytesio_content",
      "tests/test_content.py::test_multipart_data_and_files_content",
      "tests/test_content.py::test_response_bytes_content"
    ])
    assert set(produced_data[0]['FAIL_TO_PASS']) == set([ # HTTPX
      "tests/test_template.py::test_jinja_raise_for_invalid_path",
      "tests/app/test_error_handling.py::test_default_handle_http_exception_handling",
      "tests/test_template.py::test_handler_raise_for_no_template_engine",
      "tests/test_template.py::test_mako_raise_for_invalid_path"
    ])
    assert produced_data[1]['FAIL_TO_FAIL'] == []
