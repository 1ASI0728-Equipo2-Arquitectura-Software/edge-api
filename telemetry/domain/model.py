"""
telemetry/domain/model.py

Entidades y value objects del bounded context Telemetry.

SensorReadingValue  — value object que representa una lectura individual
                      de un sensor (variable, valor numérico, unidad).
TelemetryRecord     — aggregate root que agrupa las lecturas de un envío
                      del dispositivo IoT.
AgronomicThreshold  — entidad que define los límites agronómicos aceptables
                      para una variable (temperatura, humedad, etc.).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


@dataclass(frozen=True)
class SensorReadingValue:
    """
    Value object que representa la lectura de un sensor en un instante dado.

    Inmutable: dos lecturas con los mismos valores son equivalentes.

    Attributes:
        variable:  Nombre de la variable medida (p.ej. "temperature").
        value:     Valor numérico registrado por el sensor.
        unit:      Unidad de medida (p.ej. "°C", "%", "lux").
        timestamp: Fecha y hora UTC en que se tomó la lectura.
    """

    variable: str
    value: float
    unit: str
    timestamp: datetime


@dataclass
class TelemetryRecord:
    """
    Aggregate root del bounded context Telemetry.

    Representa un lote de lecturas de sensores enviado por un dispositivo
    IoT en un momento determinado.

    Attributes:
        device_id:   Identificador del dispositivo que generó las lecturas.
        readings:    Lista de lecturas individuales de sensores.
        recorded_at: Fecha y hora UTC en que el Edge API recibió el lote.
    """

    device_id: str
    readings: List[SensorReadingValue]
    recorded_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    owner_user_id: int = 0

    def get_reading(self, variable: str) -> SensorReadingValue | None:
        """
        Retorna la primera lectura que coincida con la variable indicada.

        Args:
            variable: Nombre de la variable a buscar (p.ej. "temperature").

        Returns:
            SensorReadingValue si existe, None si la variable no está en el lote.
        """
        for reading in self.readings:
            if reading.variable == variable:
                return reading
        return None


@dataclass
class AgronomicThreshold:
    """
    Entidad que define los límites agronómicos aceptables para una variable.

    Usada por TelemetryDomainService para evaluar si una lectura está
    dentro del rango saludable para el cultivo de palma aceitera.

    Attributes:
        variable:  Nombre de la variable (p.ej. "temperature").
        min_value: Valor mínimo aceptable.
        max_value: Valor máximo aceptable.
    """

    variable: str
    min_value: float
    max_value: float

    def is_within_range(self, value: float) -> bool:
        """
        Determina si un valor está dentro del rango aceptable.

        Args:
            value: Valor numérico a evaluar.

        Returns:
            True si min_value <= value <= max_value, False en caso contrario.
        """
        return self.min_value <= value <= self.max_value
