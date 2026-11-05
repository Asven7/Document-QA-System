"""Wait until the configured database accepts connections.

Used by the Docker entrypoint so the application never starts before
PostgreSQL is ready - no manual, undocumented database setup steps.
"""

from __future__ import annotations

import sys
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Block until the configured database is ready (used at container startup)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--timeout", type=int, default=90, help="Seconds to wait (default: 90).")
        parser.add_argument("--interval", type=float, default=3.0, help="Seconds between attempts (default: 3).")

    def handle(self, *args, **options) -> None:
        timeout = options["timeout"]
        interval = options["interval"]
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None

        self.stdout.write("Waiting for the database to become available...")
        while time.monotonic() < deadline:
            try:
                connection.ensure_connection()
                connection.close()
                self.stdout.write(self.style.SUCCESS("Database is ready."))
                return
            except Exception as exc:  # noqa: BLE001 - report any connection error
                last_error = exc
                self.stdout.write(f"  database not ready ({type(exc).__name__}), retrying...")
                time.sleep(interval)

        raise CommandError(
            f"Database was not available within {timeout} seconds: {last_error}"
        )
