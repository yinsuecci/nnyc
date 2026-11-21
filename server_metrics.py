"""Host-visible resource samples; container limits must be monitored separately."""
import time

import psutil


class Sampler:
    def __init__(self, disk_path="/"):
        self.disk_path = disk_path
        self.previous = None
        self.previous_cpu = self.cpu_times()

    @staticmethod
    def cpu_times():
        values = psutil.cpu_times()
        # Linux guest times are already included in user/nice; do not count twice.
        total = sum(values) - getattr(values, "guest", 0) - getattr(values, "guest_nice", 0)
        return total, values.idle + getattr(values, "iowait", 0)

    def sample(self):
        now = time.monotonic()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self.disk_path)
        network = psutil.net_io_counters()
        total = network.bytes_sent + network.bytes_recv
        rate = 0 if self.previous is None else max(0, total - self.previous[1]) / max(.001, now - self.previous[0])
        self.previous = now, total
        cpu = self.cpu_times()
        elapsed = cpu[0] - self.previous_cpu[0]
        idle = cpu[1] - self.previous_cpu[1]
        cpu_percent = max(0, min(100, 100 * (elapsed - idle) / elapsed)) if elapsed > 0 else 0
        self.previous_cpu = cpu
        return {"cpu_percent": cpu_percent, "cpu_count": psutil.cpu_count() or 1,
                "memory_total": memory.total, "memory_used": memory.total - memory.available,
                "disk_total": disk.total, "disk_used": disk.used, "network_bps": rate,
                "scope": "host-visible (container quotas not included)"}
