"""Stream a local file to the SeaweedFS filer: python upload.py <local> <bucket> <key>."""
import http.client
import os
import sys
import time
from urllib.parse import urlparse

FILER = os.getenv("FILER_URL", "http://srb-s3:8888")


def upload(local: str, bucket: str, key: str) -> None:
    target = urlparse(FILER)
    size = os.path.getsize(local)
    started = time.perf_counter()
    conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=3600)
    with open(local, "rb") as body:
        conn.request("PUT", f"/buckets/{bucket}/{key}", body=body,
                     headers={"Content-Length": str(size)})
    response = conn.getresponse()
    response.read()
    if response.status >= 300:
        raise SystemExit(f"upload failed: HTTP {response.status}")
    print(f"{key}: {size:,} bytes in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    upload(*sys.argv[1:4])
