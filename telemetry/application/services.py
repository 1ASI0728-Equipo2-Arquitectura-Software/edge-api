"""
telemetry/application/services.py

Application service del bounded context Telemetry.

TelemetryApplicationService orquesta el caso de uso principal:
recibir un lote de lecturas del ESP32, evaluarlas contra umbrales
agronómicos, persistirlas localmente y reenviarlas al cloud.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from telemetry.domain.model import SensorReadingValue, TelemetryRecord
from telemetry.domain.services import (
    IAgronomicThresholdRepository,
    ITelemetryRepository,
    TelemetryDomainService,
)
from telemetry.infrastructure.repositories import CloudOutboxRepository

logger = logging.getLogger(__name__)


class TelemetryApplicationService:
    """
    Orquestador del caso de uso de registro de telemetría.

    Attributes:
        _telemetry_repo:  Repositorio de persistencia local de lecturas.
        _domain_service:  Servicio de dominio para evaluación de alertas.
    """

    def __init__(
        self,
        telemetry_repository: ITelemetryRepository,
        threshold_repository: IAgronomicThresholdRepository,
    ) -> None:
        self._telemetry_repo = telemetry_repository
        self._domain_service = TelemetryDomainService(threshold_repository)

    def record_telemetry(
        self,
        device_id: str,
        readings_data: list[dict[str, Any]],
    ) -> tuple[int, dict[str, bool]]:
        """
        Registra un lote de lecturas de sensores del dispositivo IoT.

        Flujo:
        1. Construye TelemetryRecord desde los datos raw del request.
        2. Evalúa alertas contra umbrales agronómicos (domain service).
        3. Persiste el record localmente (SQLite).
        4. Encola el lote para reintento durable hacia el Web Service central.
        5. Retorna (record_id, alert_flags) al caller (Blueprint).

        Args:
            device_id:     Identificador del dispositivo autenticado.
            readings_data: Lista de dicts con keys: variable, value,
                           unit, timestamp (ISO 8601 string).

        Returns:
            Tupla (record_id, alert_flags) donde:
            - record_id:   ID del registro persistido en SQLite.
            - alert_flags: Dict {variable: True si hay alerta}.

        Raises:
            ValueError: Si readings_data está vacío o algún dict tiene
                        keys faltantes.
            Exception:  Propaga errores de persistencia (no de red).
        """
        if not readings_data:
            raise ValueError("El lote de lecturas no puede estar vacío.")

        readings = self._parse_readings(readings_data)

        record = TelemetryRecord(
            device_id=device_id,
            readings=readings,
            recorded_at=datetime.now(timezone.utc),
        )

        alert_flags = self._domain_service.evaluate_alerts(record)

        record_id = self._telemetry_repo.save(record)
        CloudOutboxRepository.enqueue(record)

        logger.info(
            "Telemetría registrada: record_id=%d device_id='%s' alertas=%s.",
            record_id,
            device_id,
            {k: v for k, v in alert_flags.items() if v},
        )

        return record_id, alert_flags

    @staticmethod
    def _parse_readings(
        readings_data: list[dict[str, Any]],
    ) -> list[SensorReadingValue]:
        """
        Convierte dicts raw del request en SensorReadingValue.

        Args:
            readings_data: Lista de dicts con variable, value, unit, timestamp.

        Returns:
            Lista de SensorReadingValue listos para el TelemetryRecord.

        Raises:
            ValueError: Si algún dict tiene keys faltantes o timestamp inválido.
        """
        required_keys = {"variable", "value", "unit", "timestamp"}
        readings = []

        for i, item in enumerate(readings_data):
            missing = required_keys - item.keys()
            if missing:
                raise ValueError(
                    f"Lectura [{i}] le faltan los campos: {missing}."
                )

            try:
                timestamp = datetime.fromisoformat(item["timestamp"])
            except (ValueError, TypeError):
                logger.warning(
                    "Lectura [%d] tiene timestamp inválido '%s' — usando hora de recepción.",
                    i,
                    item["timestamp"],
                )
                timestamp = datetime.now(timezone.utc)

            readings.append(
                SensorReadingValue(
                    variable=str(item["variable"]),
                    value=float(item["value"]),
                    unit=str(item["unit"]),
                    timestamp=timestamp,
                )
            )

        return readings
