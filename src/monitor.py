import psutil
import time
import os
from datetime import datetime

class SystemMonitor:
    def __init__(self):
        self.os_info = "Linux/Android"
    
    def get_cpu_usage(self):
        """
        Returns CPU usage. Handles Android permission restrictions gracefully.
        """
        try:
     
            return psutil.cpu_percent(interval=1)
        except PermissionError:

            try: 
                load1, _, _ = os.getloadavg()
     
                cpu_count = os.cpu_count() or 1
                usage = (load1 / cpu_count) * 100
                return round(usage, 2)
            except:
                return "N/A (Restricted)"

    def get_memory_usage(self):
        """Returns a dict containing total, available, and percent usage."""
        try:
            mem = psutil.virtual_memory()
            return {
                "total": f"{mem.total / (1024**3):.2f} GB",
                "available": f"{mem.available / (1024**3):.2f} GB",
                "percent": mem.percent
            }
        except PermissionError:
             return {"total": "?", "available": "?", "percent": "N/A"}

    def get_disk_usage(self):
        """Returns disk usage percentage for root."""
        try:
            disk = psutil.disk_usage('/')
            return disk.percent
        except:
            return 0

    def get_report(self):
        """Generates a full status report."""
        cpu = self.get_cpu_usage()
        mem = self.get_memory_usage()
        disk = self.get_disk_usage()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

     
        cpu_display = f"{cpu}%" if isinstance(cpu, (int, float)) else cpu

        return (
            f"[{timestamp}] STATUS REPORT:\n"
            f"---------------------------\n"
            f"CPU Load  : {cpu_display}\n"
            f"RAM Usage : {mem['percent']}% (Free: {mem['available']})\n"
            f"Disk Usage: {disk}%\n"
            f"---------------------------"
        )


if __name__ == "__main__":
    mon = SystemMonitor()
    print("Running diagnostic test...")
    print(mon.get_report())
