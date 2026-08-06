"""Rebuild the bundled sample dataset from the full TLC monthly file.

Downloads the full yellow-taxi parquet for one month from the NYC TLC
CloudFront mirror plus the taxi-zone lookup CSV, then writes a deterministic
sample (every 61st trip by pickup time, ~48k rows for a typical month) to
``data/yellow_tripdata_sample.parquet`` so the repository stays small while
the data remains real.

Usage:
    python scripts/download_data.py [YYYY-MM]   # default 2024-01
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import duckdb

CDN = "https://d37ci6vzurychx.cloudfront.net"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLE_STEP = 61  # keep 1 of every 61 trips -> ~48k rows for Jan 2024


def main() -> int:
    month = sys.argv[1] if len(sys.argv) > 1 else "2024-01"
    year, mon = month.split("-")
    full_path = DATA_DIR / f"yellow_tripdata_{month}_full.parquet"
    url = f"{CDN}/trip-data/yellow_tripdata_{month}.parquet"
    print(f"Downloading {url} ...")
    urllib.request.urlretrieve(url, full_path)
    urllib.request.urlretrieve(
        f"{CDN}/misc/taxi_zone_lookup.csv", DATA_DIR / "taxi_zone_lookup.csv"
    )

    sample_path = DATA_DIR / "yellow_tripdata_sample.parquet"
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (
              ORDER BY tpep_pickup_datetime, tpep_dropoff_datetime
            ) AS rn
            FROM read_parquet('{full_path}')
            WHERE tpep_pickup_datetime >= TIMESTAMP '{year}-{mon}-01'
              AND tpep_pickup_datetime < TIMESTAMP '{year}-{mon}-01'
                  + INTERVAL 1 MONTH
          ) WHERE rn % {SAMPLE_STEP} = 1
        ) TO '{sample_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{sample_path}')"
    ).fetchone()[0]
    full_path.unlink()
    print(f"Wrote {sample_path} with {rows:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
