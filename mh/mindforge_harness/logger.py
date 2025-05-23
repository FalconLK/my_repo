"""Logger setup for the project."""
import logging
import os
import sys

from tqdm.asyncio import tqdm


class MindForgeHarnessLogger:
    """Set up the logger for logging the build process of images and containers."""
    
    def __init__(self, logger_name: str, log_file: str=None, mode: str="w", add_stdout: bool=False):        
        """Set up the logger for logging the build process of images and containers.

        It writes logs to the log file.

        If `add_stdout` is True, logs will also be sent to stdout, which can be used for
        streaming ephemeral output from Modal containers.
        """
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.DEBUG)  # Set the lowest level to capture all logs

        if not self.logger.handlers:
            # File handler (Debug level)
            if log_file:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                # If the file already exists, flush the contents
                if os.path.exists(log_file) and mode == "w":
                    open(log_file, "w").close()
                file_handler = logging.FileHandler(log_file, mode=mode, encoding="utf-8")
                file_handler.setLevel(logging.NOTSET)  # Only logs DEBUG and higher
                file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s") 
                file_handler.setFormatter(file_formatter)
                self.logger.addHandler(file_handler)
                setattr(self.logger, "log_file", log_file)

            # Stdout handler (Info level)
            if add_stdout:
                stdout_handler = logging.StreamHandler(sys.stdout)
                stdout_handler.setLevel(logging.INFO)  # Only logs INFO and higher
                stdout_formatter = logging.Formatter(f"%(asctime)s - {logger_name} - %(levelname)s - %(message)s")
                stdout_handler.setFormatter(stdout_formatter)
                self.logger.addHandler(stdout_handler)

        self.logger.propagate = False

    def __enter__(self) -> logging.Logger:
        """Return the logger object."""
        return self.logger
    
    def __exit__(self, exc_type, exc_value, traceback):
        """Close the logger and its handlers."""
        self.close()

    def close(self):
        """Close the logger and its handlers."""
        for handler in self.logger.handlers:
            handler.close()
            self.logger.removeHandler(handler)

class TqdmLoggingHandler(logging.Handler):
    """Custom logging handler for tqdm progress bar."""
    
    def emit(self, record):
        """Emit the log record."""
        try:
            msg = self.format(record)
            tqdm.write(msg)  # Use tqdm.write() instead of print()
        except Exception:
            self.handleError(record)

class TQDMLogger(MindForgeHarnessLogger):
    """Logger for tqdm progress bar."""
    
    def __init__(self, logger_name: str, log_file: str):
        """Set up the logger for tqdm progress bar."""
        super().__init__(logger_name, log_file=log_file, add_stdout=False, mode="a")

        self.logger.setLevel(logging.DEBUG)
        
        if not any(isinstance(h, TqdmLoggingHandler) for h in self.logger.handlers):
            handler = TqdmLoggingHandler()
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        