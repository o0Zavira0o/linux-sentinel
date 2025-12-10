import time
import sys
from src.monitor import SystemMonitor
from src.logger import SentinelLogger
from src.watchdog import ServiceWatchdog 

def start_sentinel():
    print("🛡️  Linux Sentinel Started...")
    
    monitor = SystemMonitor()
    logger = SentinelLogger()
    


    watchdog = ServiceWatchdog("dummy_service.py", "python dummy_service.py")
    
    logger.info("Sentinel started monitoring & watching services.")

    try:
        while True:

            report = monitor.get_report()
            print("\033c", end="")
            print(report)
            
            status = "✅ Service is Running"
            if watchdog.is_running():
                print(f"\n🐕 Watchdog: {status}")
            else:
                print(f"\n🐕 Watchdog: ⚠️ Service DEAD! Restarting...")
                watchdog.check_and_heal() 

            time.sleep(2)
            
    except KeyboardInterrupt:
        logger.info("Sentinel stopped.")
        sys.exit(0)

if __name__ == "__main__":
    start_sentinel()
