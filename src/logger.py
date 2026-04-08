import os
import logging
from pathlib import Path

def setup_logger() -> logging.Logger:
    """
    Creates and configures a centralized logger for the application.
    The log level is determined by the LOG_LEVEL environment variable,
    defaulting to INFO. Supported levels: DEBUG, INFO, WARNING, ERROR.
    """
    logger = logging.getLogger("spicy-agent")
    
    # Do not add duplicate handlers if logger is already configured
    if logger.handlers:
        return logger

    # Determine log level from environment
    env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, env_level, logging.INFO)
    logger.setLevel(numeric_level)
    
    # Setup console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)  # Let the logger's level control the output
    
    # Format: [TIME] [LEVEL] MESSAGE
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    ch.setFormatter(formatter)
    
    logger.addHandler(ch)
    return logger


def enable_runtime_log_file(debug_dir: Path | None) -> None:
    """Mirror logger output to ``debug_dir/runtime.log`` when a debug dir is set."""
    if debug_dir is None:
        return

    debug_dir.mkdir(parents=True, exist_ok=True)
    log_path = debug_dir / "runtime.log"
    logger = logging.getLogger("spicy-agent")

    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path:
            return

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    )
    logger.addHandler(file_handler)

# Export a default instance
logger = setup_logger()
