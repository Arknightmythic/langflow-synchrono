"""Sample container and StarRocks BE usage every few seconds into a CSV.

python sampler.py <output.csv> <container> [<container> ...]
Needs /var/run/docker.sock mounted; StarRocks is sampled when STARROCKS_PASSWORD is set.
"""
import csv
import http.client
import json
import os
import socket
import sys
import time

sys.path.insert(0, "/srv")

INTERVAL = float(os.getenv("SAMPLE_SECONDS", "5"))


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


def main(path: str, containers: list[str]) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ts", "source", "cpu_pct", "mem_mb_or_pct"])
        while True:
            now = round(time.time(), 1)
            for name in containers:
                stats = container_stats(name)
                if stats:
                    writer.writerow([now, name, *stats])
            be = starrocks_stats()
            if be:
                writer.writerow([now, "starrocks-be", *be])
            fh.flush()
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
