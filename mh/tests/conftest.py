import os
import shutil

def pytest_sessionstart(session):
    logs_dir = "logs"
    if os.path.exists(logs_dir):
        shutil.rmtree(logs_dir)
    os.makedirs(logs_dir)  # Recreate the folder if needed