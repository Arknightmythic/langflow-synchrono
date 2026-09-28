#!/usr/bin/env python3
"""
Calibration Harness: Compares LLM-as-a-judge scores against human ground-truth labels.
Computes:
  - Cohen's Kappa for categorical scales (factuality, template, conciseness)
  - Raw agreement rate
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def cohens_kappa(a: list, b: list) -> float:
    assert len(a) == len(b) and a, "Need equal non-empty sequences"
    labels = sorted(set(a) | set(b), key=lambda x: str(x))
    idx = {label: i for i, label in enumerate(labels)}
    n = len(labels)
    mat = [[0] * n for _ in range(n)]
    for x, y in zip(a, b):
        mat[idx[x]][idx[y]] += 1
    total = len(a)
    observed = sum(mat[i][i] for i in range(n)) / total
    row = [sum(mat[i]) / total for i in range(n)]
    col = [sum(mat[i][j] for i in range(n)) / total for j in range(n)]
    expected = sum(row[i] * col[i] for i in range(n))
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def main():
    parser = argparse.ArgumentParser(description="Calibrate LLM judges against human labels")
    parser.add_argument("--human", type=Path, required=True, help="Path to human labels CSV (id,criterion,score)")
    parser.add_argument("--judge", type=Path, required=True, help="Path to judge results JSON")
    args = parser.parse_args()

    # Load judge results
    judge_data = json.loads(args.judge.read_text())
    judge_scores: dict[str, dict[str, str]] = {}
    for rec in judge_data:
        rec_id = rec.get("id")
        verdicts = rec.get("verdicts", {})
        judge_scores[rec_id] = {crit: v.get("score") for crit, v in verdicts.items()}

    # Load human labels
    human_scores: dict[str, dict[str, str]] = defaultdict(dict)
    with open(args.human, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            human_scores[row["id"]][row["criterion"]] = row["score"].strip().lower()

    # Match pairs per criterion
    criterion_pairs = defaultdict(lambda: ([], []))
    for rec_id, h_crits in human_scores.items():
        if rec_id in judge_scores:
            j_crits = judge_scores[rec_id]
            for crit, h_val in h_crits.items():
                if crit in j_crits:
                    j_val = str(j_crits[crit]).strip().lower()
                    criterion_pairs[crit][0].append(h_val)
                    criterion_pairs[crit][1].append(j_val)

    print("=" * 80)
    print(" LLM JUDGE CALIBRATION REPORT AGAINST HUMAN LABELS")
    print("=" * 80)

    for crit, (h_list, j_list) in criterion_pairs.items():
        if not h_list:
            continue
        agree = sum(1 for h, j in zip(h_list, j_list) if h == j) / len(h_list)
        try:
            kappa = cohens_kappa(h_list, j_list)
        except Exception:
            kappa = 1.0 if agree == 1.0 else 0.0

        status = "PASSED (>= 0.60)" if kappa >= 0.60 else "NEEDS ITERATION (< 0.60)"
        print(f"Criterion: {crit:<25}")
        print(f"  • Samples Paired     : {len(h_list)}")
        print(f"  • Raw Agreement      : {agree * 100:.1f}%")
        print(f"  • Cohen's Kappa      : {kappa:.3f} [{status}]\n")

    print("=" * 80)


if __name__ == "__main__":
    main()
