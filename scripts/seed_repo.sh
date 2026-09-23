#!/usr/bin/env bash
# Build the checkout-api source repository the agent inspects.
#
# This is a real git repository with a real history, not a fixture file, because
# the agent's git toolset runs actual git commands against it. The history is
# arranged so that each failure mode has a corresponding commit an investigation
# can find: the nullable-shipping_cost change that breaks v1.4.2, and the
# hardcoded credential that the secret scan should flag.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$ROOT/seedapp/repo"

rm -rf "$REPO"
mkdir -p "$REPO/checkout"
cd "$REPO"
git init -q -b main
git config user.name "platform-engineering"
git config user.email "platform@example.internal"
git config commit.gpgsign false

commit() { GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"; }

# --- v1.4.0 -----------------------------------------------------------------
cat > checkout/models.py <<'EOF'
from decimal import Decimal

from sqlalchemy import Column, Numeric, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Cart(Base):
    __tablename__ = "carts"

    cart_id = Column(String, primary_key=True)
    subtotal = Column(Numeric(10, 2), nullable=False)
    shipping_cost = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
EOF

cat > checkout/handler.py <<'EOF'
"""Order creation."""

import logging

log = logging.getLogger(__name__)


def create_order(cart, repository):
    """Persist an order for the given cart and return its id."""
    total = cart.subtotal + cart.shipping_cost
    order_id = repository.insert(
        cart_id=cart.cart_id,
        subtotal=cart.subtotal,
        shipping_cost=cart.shipping_cost,
        total=total,
    )
    log.info("created order %s for cart %s totalling %s", order_id, cart.cart_id, total)
    return order_id
EOF

cat > checkout/settings.py <<'EOF'
"""Runtime settings, read from the environment."""

import os

DATABASE_URL = os.environ["DATABASE_URL"]
PAYMENTS_API_BASE = os.environ.get("PAYMENTS_API_BASE", "https://payments.internal")
REPORT_BUCKET = os.environ.get("REPORT_BUCKET", "checkout-reports")

# Credentials are injected by the deployment; never hardcode them here.
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
EOF

cat > README.md <<'EOF'
# checkout-api

Order creation and revenue reporting for the storefront.

## Releases

Deployments are tagged. Roll back with:

    kubectl set image deployment/checkout-api checkout-api=registry.internal/checkout-api:<tag> -n <env>
EOF

git add -A
commit "2026-09-10T11:02:00+0000" "feat: initial checkout service

Order creation, revenue reporting and the settings module."
git tag v1.4.0

# --- v1.4.1 -----------------------------------------------------------------
cat > Dockerfile <<'EOF'
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY checkout/ ./checkout/
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "checkout.wsgi:app"]
EOF
printf 'flask==3.1.0\nsqlalchemy==2.0.36\npsycopg[binary]==3.2.3\ngunicorn==23.0.0\n' > requirements.txt
git add -A
commit "2026-09-17T09:12:44+0000" "chore: bump base image to python:3.12-slim

No functional change. Picks up the upstream OpenSSL patch."
git tag v1.4.1

# --- the leaked credential --------------------------------------------------
# Committed as a "temporary" debugging convenience, which is how this nearly
# always happens in practice.
cat > checkout/reporting.py <<'EOF'
"""Nightly revenue export to S3."""

import boto3

# TODO(remove before merge): hardcoded for the staging export run on 2026-09-17.
# The deploy pipeline was not injecting credentials into the cron pod yet.
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7LEAKED1"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

BUCKET = "checkout-reports"


def upload_revenue_csv(path: str, key: str) -> str:
    client = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )
    client.upload_file(path, BUCKET, key)
    return f"s3://{BUCKET}/{key}"
EOF
git add -A
commit "2026-09-17T18:44:10+0000" "feat(reporting): nightly revenue export to S3

Temporary credentials inline so the staging cron can run tonight; the
pipeline will inject them properly next sprint."

# --- v1.4.2, the bad deploy -------------------------------------------------
cat > checkout/models.py <<'EOF'
from decimal import Decimal

from sqlalchemy import Column, Numeric, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Cart(Base):
    __tablename__ = "carts"

    cart_id = Column(String, primary_key=True)
    subtotal = Column(Numeric(10, 2), nullable=False)
    shipping_cost = Column(Numeric(10, 2), nullable=True)
EOF
git add -A
commit "2026-09-18T13:41:02+0000" "feat(checkout): make shipping_cost optional for digital goods

Digital goods have no shipping, so allow the column to be null rather than
defaulting it to zero. Saves a pointless write on every digital order."
git tag v1.4.2

git log --oneline --decorate | sed 's/^/  /'
echo "repository ready at seedapp/repo ($(git rev-list --count HEAD) commits)"
