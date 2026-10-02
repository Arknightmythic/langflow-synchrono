"""End-to-end smoke test through the HTTP API: python e2e.py <base_url> <file_id> <s3_key> <master_id> <master_key>."""
import json
import sys
import time
import urllib.error
import urllib.request

HARNESS = "http://srb-cb:8000"
KEY = {"x-api-key": "bench-key"}


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **KEY,
                                              **(headers or {})})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.status, json.loads(response.read())


def main(base, file_id, key, master_id, master_key):
    started = time.time()
    code, body = call("POST", f"{base}/api/v1/grading/jobs", {
        "fileId": file_id, "s3Bucket": "bench", "rawSourceKey": key, "parquetKey": key,
        "enrichedParquetKey": f"{file_id}/enriched.parquet",
        "callback": {"url": f"{HARNESS}/cb/grading"}})
    print("grading dispatch", code, body)
    while True:
        time.sleep(2)
        _, status = call("GET", f"{base}/api/v1/grading/jobs/{file_id}")
        if status.get("done"):
            break
    summary = (status.get("result") or {}).get("summary") or {}
    print(f"grading {status['status']} in {time.time() - started:.1f}s: grade "
          f"{summary.get('gradeLetter')} score {summary.get('qualityScore')} rows "
          f"{status.get('recordCount')} error {status.get('error')}")
    job_id = f"e2e-{file_id}-{int(time.time())}"
    try:
        call("POST", f"{HARNESS}/prepare", {"jobId": job_id, "fileId": file_id,
                                           "masterFileId": master_id})
    except urllib.error.URLError:
        pass
    payload = {"jobId": job_id, "fileId": file_id, "masterFileId": master_id, "actor": "bench",
               "callbackUrl": f"{HARNESS}/cb/matching", "s3Bucket": "bench",
               "incomingFile": {"s3Key": f"{file_id}/enriched.parquet"},
               "masterDataFile": {"s3Key": master_key}}
    started = time.time()
    code, body = call("POST", f"{base}/api/v1/run/matching-dispatch",
                      {"tweaks": {"MatchingDispatch-b4819": {"payload": json.dumps(payload)}}})
    text = body["outputs"][0]["outputs"][0]["results"]["message"]["text"]
    print("matching dispatch", code, text)
    while True:
        time.sleep(3)
        try:
            _, callback = call("GET", f"{HARNESS}/cb/{job_id}")
            break
        except urllib.error.HTTPError:
            continue
    metrics = callback["body"].get("metrics") or {}
    print(f"matching {callback['body']['status']} in {time.time() - started:.1f}s: "
          f"AUTO {metrics.get('autoCount')} REVIEW {metrics.get('reviewCount')} "
          f"UNMATCH {metrics.get('unmatchCount')} CONFLICT {metrics.get('conflictCount')} "
          f"stages {metrics.get('stageDurations')} error {callback['body'].get('error')}")


if __name__ == "__main__":
    main(*sys.argv[1:6])
