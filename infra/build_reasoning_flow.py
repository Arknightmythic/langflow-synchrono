"""
Build both reasoning flows in Langflow via API:
  - reasoning-dispatch (API 1: queue reasoning job, release worker, return 202)
  - reasoning-status   (API 2: polling status)

Usage:
    python build_reasoning_flow.py

Idempotent: Re-running updates existing flows rather than duplicating them.
"""

import json
import os
from pathlib import Path

from flow_util import BASE, bangun, katalog, masuk, simpan_contoh

CURRENT_DIR = Path(__file__).parent

DISPATCH_CHAIN = [
    ("ReasoningDispatch", None, "accepted"),
    ("ChatOutput", "input_value", "message"),
]

STATUS_CHAIN = [
    ("ReasoningStatus", None, "status"),
    ("ChatOutput", "input_value", "message"),
]


def main() -> int:
    token = masuk()
    print("Logged in to Langflow successfully.")

    catalog_components = katalog(token, "reasoning")
    component_names = sorted(c for c in catalog_components if c != "ChatOutput")
    print(f"Registered components in category 'reasoning': {len(component_names)}")
    for c in component_names:
        print(f"   - {c}")
    print()

    flow_id_dispatch, node_dispatch = bangun(
        token,
        "Synchrono Reasoning Dispatch",
        "API 1: Queue AI reasoning job, release background worker, return 202 QUEUED.",
        "reasoning-dispatch",
        catalog_components,
        DISPATCH_CHAIN,
    )

    flow_id_status, node_status = bangun(
        token,
        "Synchrono Reasoning Status",
        "API 2: Check status and progress of an AI reasoning job (polling).",
        "reasoning-status",
        catalog_components,
        STATUS_CHAIN,
    )

    # Generate sample Postman request files
    sample_dispatch = {
        "output_type": "chat",
        "input_type": "text",
        "input_value": "",
        "tweaks": {
            node_dispatch: {
                "payload": json.dumps(
                    {
                        "fileId": "csv_1789441689523_iq5ws",
                        "llmModel": "llama3.1:8b-instruct-q4_K_M",
                    },
                    ensure_ascii=False,
                )
            }
        },
    }

    sample_status = {
        "output_type": "chat",
        "input_type": "text",
        "input_value": "",
        "tweaks": {
            node_status: {
                "file_id": "csv_1789441689523_iq5ws"
            }
        },
    }

    simpan_contoh(CURRENT_DIR / "postman_reasoning_dispatch.json", sample_dispatch)
    simpan_contoh(CURRENT_DIR / "postman_reasoning_status.json", sample_status)

    separator = "=" * 74
    print(f"\n{separator}")
    print("  API 1: REASONING DISPATCH")
    print(f"    POST {BASE}/api/v1/run/reasoning-dispatch?stream=false")
    print(f"    Canvas    : {BASE}/flow/{flow_id_dispatch}")
    print(f"    Node ID   : {node_dispatch}")
    print(f"    Sample    : infra/postman_reasoning_dispatch.json")
    print()
    print("  API 2: REASONING STATUS (Polling)")
    print(f"    POST {BASE}/api/v1/run/reasoning-status?stream=false")
    print(f"    Canvas    : {BASE}/flow/{flow_id_status}")
    print(f"    Node ID   : {node_status}")
    print(f"    Sample    : infra/postman_reasoning_status.json")
    print(separator)
    print("\n  Required Headers:")
    print("    x-api-key: <API KEY>")
    print("    Content-Type: application/json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
