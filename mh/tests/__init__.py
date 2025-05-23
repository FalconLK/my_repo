"""Utility functions and fixtures for tests."""
import shutil
from pathlib import Path

import pytest


def get_test_data_path(file_name: str) -> str:
    """Return the absolute path to a test data file.
    
    Example: `get_test_data_path("custom_test_data.jsonl")`.
    """
    test_dir = Path(__file__).resolve().parent

    data_path = test_dir / "test_data" / file_name
    
    if not data_path.exists():
        raise FileNotFoundError(f"Test data not found at: {data_path}")
    
    return str(data_path)

