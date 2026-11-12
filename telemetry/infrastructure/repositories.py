"""
telemetry/infrastructure/repositories.py

Implementaciones SQLite de los repositorios del bounded context Telemetry.

TelemetryRepository          — persiste TelemetryRecord y sus SensorReadingValue
                               en una transacción atómica.
AgronomicThresholdRepository — persiste y consulta umbrales agronómicos sincronizados
                               desde la nube.
"""

import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from telemetry.domain.model import AgronomicThreshold, SensorReadingValue, TelemetryRecord
from telemetry.domain.time import normalize_utc, to_utc_iso, utc_now
from telemetry.domain.services import IAgronomicThresholdRepository, ITelemetryRepository
from telemetry.infrastructure.database import (
    AgronomicThresholdModel,
    SensorReadingModel,
    TelemetryRecordModel,
    CloudOutboxModel,
)
from shared.infrastructure.database import db

logger = logging.getLogger(__name__)


class TelemetryRepository(ITelemetryRepository):
    """
    Implementación de ITelemetryRepository sobre SQLite via Peewee.

    Inserta el encabezado del lote (TelemetryRecordModel) y cada lectura
    individual (SensorReadingModel) dentro de una misma transacción atómica.
    Si cualquier inserción falla, toda la operación se revierte.
    """

    @staticmethod
    def save(record: TelemetryRecord) -> int:
        """
        Persiste un TelemetryRecord completo en la base de datos.

        Crea primero el registro de encabezado y luego inserta cada
        SensorReadingValue asociado en bulk para minimizar round-trips.

        Args:
            record: Aggregate root con las lecturas del dispositivo.

        Returns:
            ID entero del TelemetryRecordModel creado.

        Raises:
            Exception: Propaga cualquier error de base de datos tras revertir
                       la transacción.
        """
        with db.atomic():
            telemetry_model = TelemetryRecordModel.create(
                device_id=record.device_id,
                owner_user_id=record.owner_user_id,
                recorded_at=normalize_utc(record.recorded_at),
            )

            reading_rows = [
                {
                    "record": telemetry_model.id,
                    "owner_user_id": record.owner_user_id,
                    "variable": reading.variable,
                    "value": reading.value,
                    "unit": reading.unit,
                    "timestamp": normalize_utc(reading.timestamp),
                }
                for reading in record.readings
            ]

            if reading_rows:
                SensorReadingModel.insert_many(reading_rows).execute()

            logger.debug(
                "TelemetryRecord id=%d guardado: device_id='%s', %d lecturas.",
                telemetry_model.id,
                record.device_id,
                len(reading_rows),
            )

        return telemetry_model.id

    @staticmethod
    def find_by_owner_user_id(owner_user_id: int) -> list[TelemetryRecordModel]:
        return list(
            TelemetryRecordModel.select()
            .where(TelemetryRecordModel.owner_user_id == owner_user_id)
            .order_by(TelemetryRecordModel.recorded_at)
        )

    @staticmethod
    def export_by_owner_user_id(owner_user_id: int) -> list[dict]:
        records = TelemetryRepository.find_by_owner_user_id(owner_user_id)
        return [
            {
                "id": record.id,
                "device_id": record.device_id,
                "owner_user_id": record.owner_user_id,
                "recorded_at": record.recorded_at.isoformat(),
                "readings": [
                    {
                        "variable": reading.variable,
                        "value": reading.value,
                        "unit": reading.unit,
                        "timestamp": reading.timestamp.isoformat(),
                        "owner_user_id": reading.owner_user_id,
                    }
                    for reading in record.readings
                ],
            }
            for record in records
        ]


class AgronomicThresholdRepository(IAgronomicThresholdRepository):
    """
    Implementación de IAgronomicThresholdRepository sobre SQLite via Peewee.

    Almacena los umbrales sincronizados desde la nube. La tabla tiene un
    unique constraint en `variable`, por lo que save() hace upsert: crea
    si no existe o actualiza min_value/max_value si ya hay un registro.
    """

    @staticmethod
    def find_by_variable(variable: str) -> Optional[AgronomicThreshold]:
        """
        Busca el umbral agronómico activo para una variable.

        Args:
            variable: Nombre de la variable (p.ej. "temperature").

        Returns:
            AgronomicThreshold si existe en la tabla, None si no hay
            umbral sincronizado para esa variable.
        """
        try:
            record = AgronomicThresholdModel.get(
                AgronomicThresholdModel.variable == variable
            )


            return AgronomicThreshold(
                variable=record.variable,
                min_value=record.min_value,
                max_value=record.max_value,
            )
        except AgronomicThresholdModel.DoesNotExist:
            return None

    @staticmethod
    def save(threshold: AgronomicThreshold) -> None:
        """
        Persiste o actualiza un umbral agronómico (upsert por variable).

        Si ya existe un registro para la variable, actualiza min_value y
        max_value. Si no existe, lo crea.

        Args:
            threshold: Entidad AgronomicThreshold a guardar.
        """
        record, created = AgronomicThresholdModel.get_or_create(
            variable=threshold.variable,
            defaults={
                "min_value": threshold.min_value,
                "max_value": threshold.max_value,
            },
        )

        if not created:
            record.min_value = threshold.min_value
            record.max_value = threshold.max_value
            record.save()
            logger.debug(
                "Umbral actualizado: variable='%s' [%.1f, %.1f].",
                threshold.variable,
                threshold.min_value,
                threshold.max_value,
            )
        else:
            logger.debug(
                "Umbral creado: variable='%s' [%.1f, %.1f].",
                threshold.variable,
                threshold.min_value,
                threshold.max_value,
            )


class CloudOutboxRepository:
    """Persistencia de reintentos de entrega hacia el backend central."""

    @staticmethod
    def enqueue(record: TelemetryRecord) -> int:
        payload = json.dumps(
            {
                "device_id": record.device_id,
                "owner_user_id": record.owner_user_id,
                "recorded_at": to_utc_iso(record.recorded_at),
                "readings": [
                    {
                        "variable": value.variable,
                        "value": value.value,
                        "unit": value.unit,
                        "timestamp": to_utc_iso(value.timestamp),
                    }
                    for value in record.readings
                ],
            }
        )
        with db.atomic():
            return CloudOutboxModel.create(payload=payload).id

    @staticmethod
    def due(limit: int = 10) -> list[CloudOutboxModel]:
        now = utc_now()
        return list(
            CloudOutboxModel.select()
            .where(
                CloudOutboxModel.sent_at.is_null(True)
                & (CloudOutboxModel.next_attempt_at <= now)
            )
            .order_by(CloudOutboxModel.created_at)
            .limit(limit)
        )

    @staticmethod
    def mark_sent(item: CloudOutboxModel) -> None:
        item.sent_at = utc_now()
        item.last_error = None
        item.save(only=[CloudOutboxModel.sent_at, CloudOutboxModel.last_error])

    @staticmethod
    def mark_failed(item: CloudOutboxModel, error: str) -> None:
        item.attempts += 1
        delay_seconds = min(300, 2 ** min(item.attempts, 8))
        item.next_attempt_at = utc_now() + timedelta(seconds=delay_seconds)
        item.last_error = error[:1000]
        item.save(
            only=[
                CloudOutboxModel.attempts,
                CloudOutboxModel.next_attempt_at,
                CloudOutboxModel.last_error,
            ]
        )

    @staticmethod
    def to_domain(item: CloudOutboxModel) -> TelemetryRecord:
        data = json.loads(item.payload)
        return TelemetryRecord(
            device_id=data["device_id"],
            owner_user_id=int(data.get("owner_user_id", 0)),
            recorded_at=normalize_utc(datetime.fromisoformat(data["recorded_at"])),
            readings=[
                SensorReadingValue(
                    variable=value["variable"],
                    value=float(value["value"]),
                    unit=value["unit"],
                    timestamp=normalize_utc(datetime.fromisoformat(value["timestamp"])),
                )
                for value in data["readings"]
            ],
        )
