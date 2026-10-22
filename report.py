import csv
from datetime import datetime
from pathlib import Path
from typing import Mapping


REPORT_HEADERS = (
    "Row",
    "Client ID",
    "Client Name",
    "Status",
    "Message",
    "Processed At",
)


def summarize_results(results: list[Mapping[str, object]]) -> dict[str, int]:
    summary = {"success": 0, "errors": 0, "cancelled": 0, "total": len(results)}
    for result in results:
        status = result.get("status")
        if status == "SUCCESS":
            summary["success"] += 1
        elif status == "ERROR":
            summary["errors"] += 1
        elif status == "CANCELLED":
            summary["cancelled"] += 1
    return summary


def write_results_csv(
    results: list[Mapping[str, object]],
    output_path: str | Path = "automation_results.csv",
) -> Path:
    path = Path(output_path).expanduser()
    processed_at = datetime.now().isoformat()
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=REPORT_HEADERS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "Row": result.get("row", ""),
                    "Client ID": result.get("client_id", ""),
                    "Client Name": result.get("client_name", ""),
                    "Status": result.get("status", ""),
                    "Message": result.get("message", ""),
                    "Processed At": processed_at,
                }
            )
    return path
