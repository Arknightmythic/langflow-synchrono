"""
Run AI Reasoning pipeline directly via CLI without opening Langflow.

Usage:
    # Direct dry-run execution (default, safe for testing)
    python run_reasoning_local.py --file-id <fileId>

    # Live execution (persists to PostgreSQL manual_matches)
    python run_reasoning_local.py --file-id <fileId> --live

    # Test full async job queue + background worker thread + status polling
    python run_reasoning_local.py --file-id <fileId> --job
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from _reasoning import execute_reasoning
from _reasoning_jobs import build_job_payload, get_job, record_job
from _reasoning_worker import dispatch_worker
from _shared import buka_koneksi


def run_direct(args) -> int:
    """Run reasoning steps synchronously in the current process."""
    start_time = time.perf_counter()
    dry_run = not args.live

    print(f"\n--- Starting AI Reasoning (File ID: {args.file_id}, Dry Run: {dry_run}) ---")
    job = {
        "file_id": args.file_id,
        "llm_model": args.model,
        "master_parquet_path": args.master_parquet,
    }

    summary = execute_reasoning(job, dry_run=dry_run, limit=args.limit)

    print("\n" + "=" * 72)
    print("EXECUTION SUMMARY")
    print("=" * 72)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nTotal elapsed: {time.perf_counter() - start_time:.2f}s")
    return 0


def run_via_job(args) -> int:
    """Run via async job table and worker thread with polling."""
    print(f"\n--- Submitting Async Reasoning Job (File ID: {args.file_id}) ---")
    job = build_job_payload(
        {
            "fileId": args.file_id,
            "llmModel": args.model,
            "masterParquetPath": args.master_parquet,
        }
    )

    con = buka_koneksi()
    try:
        record_job(con, job)
        print(f"Job registered: {job['job_id']} (Status: QUEUED)")

        dispatch_worker(job)
        print("Worker thread dispatched; polling job status every 1 second...\n")

        while True:
            time.sleep(1)
            current = get_job(con, job_id=job["job_id"])
            if not current:
                print("Job record not found during polling.")
                break
            print(f"  [{current['status']:9s}] stage: {current.get('stage') or ''}")
            if current["status"] in ("COMPLETED", "FAILED"):
                break

        print("\n" + "=" * 72)
        print(f"FINAL JOB STATUS: {current['status']}")
        print("=" * 72)
        if current.get("error"):
            print(f"Error   : {current['error']}")
        if current.get("result"):
            print("Result  :")
            print(json.dumps(current["result"], indent=2, ensure_ascii=False))

        return 0 if current["status"] == "COMPLETED" else 1
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AI Reasoning pipeline.")
    parser.add_argument("--file-id", required=True, help="Target file ID in manual_matches")
    parser.add_argument("--master-parquet", default=None, help="Path to Master Parquet file (local path or s3://)")
    parser.add_argument("--live", action="store_true", help="Persist updates to database (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting updates to database (default)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of new patterns to resolve")
    parser.add_argument("--model", default=None, help="Override LLM model name (default: gemma3:12b)")
    parser.add_argument("--job", action="store_true", help="Execute through background worker and job table")

    args = parser.parse_args()
    if args.job:
        return run_via_job(args)
    return run_direct(args)


if __name__ == "__main__":
    raise SystemExit(main())
