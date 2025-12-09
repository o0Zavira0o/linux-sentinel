import time
import sys
from src.monitor import SystemMonitor
from src.logger import SentinelLogger  

def start_sentinel():
    print("🛡️  Linux Sentinel Started...")
    
    monitor = SystemMonitor()
    logger = SentinelLogger() 
    
    logger.info("Sentinel started monitoring.") 

    try:
        while True:
            report = monitor.get_report()
            
           
            cpu_usage = monitor.get_cpu_usage()
            
           
            print("\033c", end="")
            print(report)

           
            if isinstance(cpu_usage, (int, float)) and cpu_usage > 50:
                 logger.warning(f"High CPU Load detected: {cpu_usage}%")
            
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\n🛑 Sentinel Stopped.")
        logger.info("Sentinel stopped by user.") 
        sys.exit(0)

if __name__ == "__main__":
    start_sentinel()
