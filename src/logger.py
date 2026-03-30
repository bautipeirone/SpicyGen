import os
import logging
from typing import Literal

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

# Export a default instance
logger = setup_logger()
