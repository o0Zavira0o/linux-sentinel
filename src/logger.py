import logging
import os
from datetime import datetime

class SentinelLogger:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        self._setup_directory()
        self.logger = self._configure_logger()

    def _setup_directory(self):
        """Creates the logs directory if it doesn't exist."""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def _configure_logger(self):
        """Configures logging to file and console."""

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(self.log_dir, f"{today}.log")

        logger = logging.getLogger("LinuxSentinel")
        logger.setLevel(logging.INFO)


        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')


        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)


        # stream_handler = logging.StreamHandler()
        # stream_handler.setFormatter(formatter)


        if not logger.handlers:
            logger.addHandler(file_handler)
            # logger.addHandler(stream_handler) 

        return logger

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(f"⚠️ {message}")

    def error(self, message):
        self.logger.error(f"🔥 {message}")
