"""
iam/infrastructure/repositories.py

Implementación SQLite de IDeviceRepository usando Peewee.
DeviceRepository traduce entre DeviceModel (ORM) y Device (entidad de dominio).
"""

from typing import Optional

from iam.domain.model import Device
from iam.domain.services import IDeviceRepository
from iam.infrastructure.database import DeviceModel


class DeviceRepository(IDeviceRepository):
    """
    Implementación de IDeviceRepository sobre SQLite via Peewee.
    Traduce DeviceModel ↔ Device.
    """

    @staticmethod
    def find_by_device_id(device_id: str) -> Optional[Device]:
        """
        Busca un dispositivo por su identificador en la tabla `devices`.

        Args:
            device_id: Identificador del dispositivo.

        Returns:
            Device si existe en la base de datos, None si no está registrado.
        """
        try:
            record = DeviceModel.get(DeviceModel.device_id == device_id)
            return Device(
                device_id=record.device_id,
                api_key=record.api_key,
                created_at=record.created_at,
            )
        except DeviceModel.DoesNotExist:
            return None

    @staticmethod
    def save(device: Device) -> None:
        """
        Persiste un Device en la tabla `devices`.
        Usa get_or_create para no duplicar dispositivos existentes.

        Args:
            device: Entidad Device a persistir.
        """
        DeviceModel.get_or_create(
            device_id=device.device_id,
            defaults={
                "api_key": device.api_key,
                "created_at": device.created_at,
            },
        )

    @staticmethod
    def find_all() -> list[Device]:
        """
        Retorna todos los dispositivos registrados en la tabla `devices`.

        Returns:
            Lista de Device (vacía si no hay ninguno registrado).
        """
        return [
            Device(
                device_id=record.device_id,
                api_key=record.api_key,
                created_at=record.created_at,
            )
            for record in DeviceModel.select()
        ]
