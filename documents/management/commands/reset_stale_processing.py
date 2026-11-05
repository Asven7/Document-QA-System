"""Reset documents left in a transient state by an application restart.

Background processing lives inside the web process (see
``documents/services/background.py``). If the container stops mid-job, the
affected rows would stay ``pending``/``processing`` forever. The entrypoint
runs this command at startup so those rows become ``failed`` with a clear,
actionable message instead.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from documents.models import Document


class Command(BaseCommand):
    help = (
        "Mark documents stuck in 'pending'/'processing' (e.g. after an "
        "application restart) as failed with a reprocessing hint."
    )

    def handle(self, *args, **options) -> None:
        stale_message = (
            "Processing was interrupted by an application restart. "
            "Reprocess this document to retry."
        )
        updated = Document.objects.filter(
            status__in=[Document.Status.PENDING, Document.Status.PROCESSING]
        ).update(status=Document.Status.FAILED, error_message=stale_message)
        if updated:
            self.stdout.write(
                self.style.WARNING(
                    f"Reset {updated} interrupted document(s) to 'failed' - "
                    "reprocess them from the Admin or the API."
                )
            )
        else:
            self.stdout.write("No interrupted documents found.")
