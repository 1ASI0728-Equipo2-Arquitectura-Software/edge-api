"""Worker durable para entregar lotes locales al backend central."""

import logging
import threading
import time

from telemetry.infrastructure.cloud_client import CloudApiClient
from telemetry.infrastructure.repositories import CloudOutboxRepository

logger = logging.getLogger(__name__)
_started = False
_lock = threading.Lock()


def start_cloud_outbox_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, name="cloud-outbox", daemon=True).start()


def _run() -> None:
    while True:
        try:
            pending = CloudOutboxRepository.due()
            for item in pending:
                try:
                    delivered = CloudApiClient.post_telemetry_batch(
                        CloudOutboxRepository.to_domain(item)
                    )
                    if delivered:
                        CloudOutboxRepository.mark_sent(item)
                    else:
                        CloudOutboxRepository.mark_failed(item, "Cloud did not acknowledge the batch")
                except Exception as exc:  # noqa: BLE001
                    CloudOutboxRepository.mark_failed(item, str(exc))
            time.sleep(2 if pending else 10)
        except Exception:  # noqa: BLE001
            logger.exception("Error en el worker de cloud outbox; se reintentará.")
            time.sleep(10)
