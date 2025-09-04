import psutil
import time
from typing import Dict, Any
from common.models import NodeResources

class ResourceReporter:
    def __init__(self):
        self.last_net_io = psutil.net_io_counters()
        self.last_time = time.time()
        try:
            import docker
            self.docker_client = docker.from_env()
        except Exception:
            self.docker_client = None

    def collect(self) -> NodeResources:
        # Memory
        vm = psutil.virtual_memory()
        
        # Disk (Root partition)
        disk = psutil.disk_usage('/')
        
        # CPU
        # interval=None is non-blocking but requires a previous call or it returns 0.0 first time.
        # We might want to call it once in __init__? 
        # But psutil.cpu_percent with interval=None compares to last call. 
        # If we want accurate "current" load, we might need a small interval or rely on the agent calling it periodically.
        # The agent calls it every heartbeat.
        cpu_percent = psutil.cpu_percent(interval=None) 
        cpu_count = psutil.cpu_count(logical=True)
        
        # Estimate available CPU cores
        # If usage is 50% on 4 cores, we treat it as 2 cores available.
        cpu_available = max(0, int(cpu_count * (1 - cpu_percent / 100)))

        # GPU Check (Mock or simplified for now, as nvidia-smi bindings might not be present)
        gpu_available = False
        try:
            # simple check if nvidia-smi exists
            import subprocess
            subprocess.check_call(['nvidia-smi', '-L'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            gpu_available = True
        except Exception:
            pass

        cached_images = []
        if self.docker_client:
            try:
                # List image tags
                for img in self.docker_client.images.list():
                    if img.tags:
                        cached_images.extend(img.tags)
            except Exception:
                pass

        return NodeResources(
            cpu_total=cpu_count,
            cpu_available=cpu_available,
            memory_total_mb=int(vm.total / (1024 * 1024)),
            memory_available_mb=int(vm.available / (1024 * 1024)),
            disk_total_gb=round(disk.total / (1024**3), 2),
            disk_free_gb=round(disk.free / (1024**3), 2),
            gpu_available=gpu_available,
            cached_images=cached_images
        )

    def get_metrics_json(self) -> Dict[str, Any]:
        """Detailed metrics for logging/monitoring beyond scheduling"""
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
        
        # Network Speed Calculation
        curr_net = psutil.net_io_counters()
        curr_time = time.time()
        dt = curr_time - self.last_time
        
        bytes_sent_sec = (curr_net.bytes_sent - self.last_net_io.bytes_sent) / dt if dt > 0 else 0
        bytes_recv_sec = (curr_net.bytes_recv - self.last_net_io.bytes_recv) / dt if dt > 0 else 0
        
        self.last_net_io = curr_net
        self.last_time = curr_time

        return {
            "cpu": {
                "usage_percent": psutil.cpu_percent(interval=None),
                "per_core": cpu_per_core
            },
            "memory": dict(psutil.virtual_memory()._asdict()),
            "network": {
                "bytes_sent_sec": bytes_sent_sec,
                "bytes_recv_sec": bytes_recv_sec
            }
        }
