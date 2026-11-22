"""
One-command pipeline: extract/load, then dbt build (seeds -> models -> tests).

Each step is a "task". If a task fails, the tasks after it don't run and the
script exits non-zero - the same idea as an Airflow DAG.

Usage:  python run_pipeline.py
"""
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DBT_DIR = ROOT / "dbt_project"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pipeline")

# Absolute path so dbt and the extract script always point at the same file.
ENV = {**os.environ, "DUCKDB_PATH": str(ROOT / "data" / "warehouse.duckdb")}

STEPS = [
    ("extract_load", [sys.executable, str(ROOT / "extract" / "extract_prices.py")], ROOT),
    ("dbt_build", ["dbt", "build", "--profiles-dir", "."], DBT_DIR),
]


def main() -> int:
    pipeline_start = time.time()
    for name, cmd, cwd in STEPS:
        log.info("START  %s", name)
        t0 = time.time()
        try:
            subprocess.run(cmd, cwd=cwd, env=ENV, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.error("FAILED %s after %.1fs: %s", name, time.time() - t0, exc)
            log.error("Pipeline stopped. Downstream steps were skipped.")
            return 1
        log.info("END    %s (%.1fs)", name, time.time() - t0)
    log.info("Pipeline succeeded in %.1fs", time.time() - pipeline_start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
