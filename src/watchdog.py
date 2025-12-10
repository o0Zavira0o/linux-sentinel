import psutil
import subprocess
import time
from src.logger import SentinelLogger
class ServiceWatchdog:
    def __init__(self, target_name, start_command):
        self.target_name = target_name   
        self.start_command = start_command
        self.logger = SentinelLogger()

    def is_running(self):
        """Checks if a process with target_name is running."""

        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:

                if proc.info['cmdline'] and self.target_name in ' '.join(proc.info['cmdline']):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def check_and_heal(self):
        """If process is dead, restart it."""
        if not self.is_running():
            self.logger.warning(f"🚨 Process '{self.target_name}' is DEAD! Attempting restart...")
            
            try:

                subprocess.Popen(self.start_command, shell=True)
                self.logger.info(f"✅ Process '{self.target_name}' restarted successfully.")
                return True 
            except Exception as e:
                self.logger.error(f"❌ Failed to restart process: {e}")
                return False
        else:
            return False 
