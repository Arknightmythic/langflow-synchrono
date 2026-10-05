"""Sample container, host-process and StarRocks BE usage every few seconds into a CSV.

python sampler.py <output.csv> <container> [<container> ...]
Needs /var/run/docker.sock mounted; StarRocks is sampled when STARROCKS_PASSWORD is set.
SAMPLE_PROCESSES="label=pattern,..." also samples host processes whose command line contains
the pattern (start the sampler with --pid=host), e.g. a StarRocks installed on the host.
Containers and processes: cpu_pct in % of one core, memory in MB. starrocks-be (SHOW BACKENDS):
cpu in % of the machine, memory in % of the BE memory limit.
"""
import csv
import http.client
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/srv")

INTERVAL = float(os.getenv("SAMPLE_SECONDS", "5"))
PROCESSES = [tuple(item.split("=", 1)) for item in os.getenv("SAMPLE_PROCESSES", "").split(",")
             if "=" in item]
TICKS = os.sysconf("SC_CLK_TCK")


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost")
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


def container_stats(name: str) -> tuple[float, float] | None:
    conn = UnixConnection("/var/run/docker.sock")
    try:
        conn.request("GET", f"/containers/{name}/stats?stream=false&one-shot=false")
        response = conn.getresponse()
        if response.status != 200:
            response.read()
            return None
        s = json.loads(response.read())
    finally:
        conn.close()
    cpu = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
    system = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
    cores = s["cpu_stats"].get("online_cpus") or 1
    cpu_pct = (cpu / system) * cores * 100 if system > 0 else 0.0
    memory = s["memory_stats"].get("usage", 0) - s["memory_stats"].get("stats", {}).get("inactive_file", 0)
    return round(cpu_pct, 1), round(memory / 2**20, 1)


def _pids(pattern: str) -> list[str]:
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                command = fh.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if pattern in command:
            found.append(pid)
    return found


def _cpu_ticks(pid: str) -> int:
    with open(f"/proc/{pid}/stat") as fh:
        fields = fh.read().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])  # utime + stime


def _resident_mb(pid: str) -> float:
    with open(f"/proc/{pid}/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def process_stats(pattern: str) -> tuple[float, float] | None:
    """CPU over one second, like docker stats, and resident memory of matching host processes."""
    before = {}
    for pid in _pids(pattern):
        try:
            before[pid] = _cpu_ticks(pid)
        except OSError:
            pass
    if not before:
        return None
    started = time.monotonic()
    time.sleep(1)
    elapsed = time.monotonic() - started
    cpu, memory = 0.0, 0.0
    for pid, ticks in before.items():
        try:
            cpu += (_cpu_ticks(pid) - ticks) / TICKS / elapsed * 100
            memory += _resident_mb(pid)
        except OSError:
            pass
    return round(cpu, 1), round(memory, 1)


def starrocks_stats():
    if not os.getenv("STARROCKS_PASSWORD"):
        return None
    try:
        from engine import sr
        row = sr.query("SHOW BACKENDS")[0]
        return float(str(row.get("CpuUsedPct", "0")).rstrip(" %")), \
            float(str(row.get("MemUsedPct", "0")).rstrip(" %"))
    except Exception:  # noqa: BLE001
        return None


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None


def main(path: str, containers: list[str]) -> None:
    # All sources are read in parallel, so one round takes about as long with seven sources
    # as with two and runs stay comparable.
    with open(path, "w", newline="") as fh, ThreadPoolExecutor(max_workers=8) as pool:
        writer = csv.writer(fh)
        writer.writerow(["ts", "source", "cpu_pct", "mem_mb_or_pct"])
        while True:
            now = round(time.time(), 1)
            jobs = [(name, pool.submit(_safe, container_stats, name)) for name in containers]
            jobs += [(label, pool.submit(_safe, process_stats, pattern)) for label, pattern in PROCESSES]
            jobs.append(("starrocks-be", pool.submit(starrocks_stats)))
            for name, job in jobs:
                stats = job.result()
                if stats:
                    writer.writerow([now, name, *stats])
            fh.flush()
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
