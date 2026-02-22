import os
import psutil


def collect_metrics() -> dict:
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory().percent
    root = os.getenv("SystemDrive", "C:") + "\\"
    disk = psutil.disk_usage(root).percent
    net = psutil.net_io_counters()
    io = None
    try:
        io = psutil.disk_io_counters()
    except Exception:
        io = None
    proc_count = 0
    try:
        proc_count = len(psutil.pids())
    except Exception:
        proc_count = 0

    return {
        "ok": True,
        "stdout": "",
        "stderr": "",
        "data": {
            "cpu": cpu,
            "memory": mem,
            "disk": disk,
            "net_sent": net.bytes_sent,
            "net_recv": net.bytes_recv,
            "disk_read_bytes": io.read_bytes if io else None,
            "disk_write_bytes": io.write_bytes if io else None,
            "proc_count": proc_count,
        },
    }
