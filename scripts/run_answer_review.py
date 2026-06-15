from __future__ import annotations

import csv
from pathlib import Path

from app.core import ask


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "eval" / "formal_answer_review_v1.csv"
OUTPUT_PATH = ROOT / "data" / "eval" / "reports" / "formal_answer_review_v1_generated.csv"


def main() -> None:
    with INPUT_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for index, row in enumerate(rows, start=1):
        result = ask(row["question"], top_k=5)
        row["answer"] = result["answer"]
        row["notes"] = (
            f"generated_case={index}; citations={len(result['citations'])}; "
            f"conflicts={len(result['conflicts'])}"
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"generated={len(rows)} output={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
