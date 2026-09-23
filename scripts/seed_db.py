#!/usr/bin/env python3
"""Create and populate the seeded service's database.

The archive table is deliberately large enough that a missing index is
genuinely slow rather than theoretically slow. An investigation that concludes
"this query is slow" has to be able to measure it.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

ADMIN_DSN = f"host={ROOT / '.pgdata' / 'sockets'} port=5433 user=postgres dbname=postgres"
SEED_DSN = os.environ.get(
    "SEEDAPP_DSN", f"host={ROOT / '.pgdata' / 'sockets'} port=5433 user=postgres dbname=seedapp"
)

ARCHIVE_ROWS = 200_000

DDL = """
CREATE TABLE IF NOT EXISTS orders (
    order_id       BIGSERIAL PRIMARY KEY,
    cart_id        TEXT NOT NULL,
    subtotal       NUMERIC(10,2) NOT NULL,
    shipping_cost  NUMERIC(10,2),
    total          NUMERIC(10,2) NOT NULL,
    customer_email TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders_archive (
    order_id       BIGSERIAL PRIMARY KEY,
    customer_email TEXT NOT NULL,
    total          NUMERIC(10,2) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def main() -> int:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'seedapp'").fetchone()
        if not exists:
            conn.execute("CREATE DATABASE seedapp")
            print("created database seedapp")

    with psycopg.connect(SEED_DSN) as conn:
        conn.execute(DDL)
        conn.commit()
        n = conn.execute("SELECT count(*) FROM orders_archive").fetchone()[0]
        if n < ARCHIVE_ROWS:
            print(f"populating orders_archive with {ARCHIVE_ROWS:,} rows...")
            rng = random.Random(20260918)
            with conn.cursor().copy(
                "COPY orders_archive (customer_email, total) FROM STDIN"
            ) as copy:
                for i in range(ARCHIVE_ROWS - n):
                    copy.write_row((
                        f"Customer{rng.randint(1, 4000)}@Example.com",
                        round(rng.uniform(5, 500), 2),
                    ))
            conn.commit()
        conn.execute(
            "CREATE INDEX IF NOT EXISTS orders_archive_email_lower_idx "
            "ON orders_archive (lower(customer_email))"
        )
        conn.commit()
        print("orders:", conn.execute("SELECT count(*) FROM orders").fetchone()[0])
        print("orders_archive:", conn.execute("SELECT count(*) FROM orders_archive").fetchone()[0])
        conn.execute("ANALYZE orders_archive")
        conn.commit()
    print("seed database ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
