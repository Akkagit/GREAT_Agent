from __future__ import annotations

import argparse
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("success", "fail", "missing", "slow"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stations", required=True)
    args = parser.parse_args()
    print(f"mock solver mode={args.mode}", flush=True)
    if args.mode == "fail":
        return 7
    if args.mode == "slow":
        time.sleep(30)
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stations = args.stations.split(",")
    if args.mode == "missing":
        stations = stations[:-1]
    for station in stations:
        (args.output_dir / f"{station}.flt").write_text(
            "solution\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
