"""Load a master parquet into StarRocks: python tools/load_master.py <master_id> <path or s3 uri>."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import master  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    started = time.perf_counter()
    result = master.load(sys.argv[1], sys.argv[2],
                         report=lambda stage: print(f"[{time.perf_counter() - started:7.1f}s] "
                                                    f"{stage}", flush=True))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
