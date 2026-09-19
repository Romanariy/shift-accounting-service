#!/usr/bin/env sh
# Run only against an isolated staging Compose project, never against a live bot's DB.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
docker compose --profile bot config --quiet
docker compose run --rm backend python manage.py check
docker compose run --rm backend python manage.py makemigrations --check --dry-run
# Django creates/drops a separate test database. Includes real PostgreSQL claim concurrency.
docker compose run --rm backend python manage.py test apps.offers apps.ledger apps.shifts --noinput
docker compose run --rm --no-deps recognition python scripts/check_ocr_offline.py
docker compose exec -T recognition python manage.py run_recognition_worker --healthcheck
docker compose exec -T offers python manage.py run_offers_worker --healthcheck
if [ -n "${OFFER_TEST_ARCHIVE:-}" ]; then
    case "$OFFER_TEST_ARCHIVE" in /*) ;; *) echo "OFFER_TEST_ARCHIVE must be an absolute path" >&2; exit 1 ;; esac
    docker compose run --rm --no-deps -v "$OFFER_TEST_ARCHIVE:/data/DS.zip:ro" recognition \
        python scripts/benchmark_offers.py /data/DS.zip --models /opt/ocr/models --output /tmp/offer-evaluation --offline
fi
echo "Automated checks passed. Complete the real Telegram and authenticated HTTP checklist in docs/shift-offers.md."
