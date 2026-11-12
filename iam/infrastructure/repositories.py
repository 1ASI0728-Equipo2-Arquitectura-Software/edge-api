"""
iam/infrastructure/repositories.py

Implementación SQLite de IDeviceRepository usando Peewee.
DeviceRepository traduce entre DeviceModel (ORM) y Device (entidad de dominio).
"""

from datetime import datetime
from typing import Optional

from iam.domain.model import Device, credential_hash
from iam.domain.services import IDeviceRepository
from iam.infrastructure.database import DeviceModel


def _to_domain(record: DeviceModel) -> Device:
    # Migración perezosa de instalaciones antiguas: convierte la API key
    # histórica en hash y elimina el texto plano antes de devolver el dominio.
    if record.api_key and not record.api_key_hash:
        migrated_hash = credential_hash(record.api_key)
        (DeviceModel
         .update(api_key_hash=migrated_hash, api_key="")
         .where(DeviceModel.id == record.id)
         .execute())
        record.api_key_hash = migrated_hash
        record.api_key = ""
    return Device(
        device_id=record.device_id,
        bootstrap_key_hash=(record.bootstrap_key_hash or "").strip().lower(),
        api_key_hash=(record.api_key_hash or "").strip().lower(),
        owner_user_id=record.owner_user_id or 0,
        plantation_id=record.plantation_id or 0,
        sector_id=record.sector_id or 0,
        is_provisioned=bool(record.is_provisioned),
        provisioned_at=record.provisioned_at,
        revoked_at=record.revoked_at,
        is_active=bool(record.is_active),
        created_at=record.created_at,
    )


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
            return _to_domain(record)
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
                "api_key": "",
                "bootstrap_key_hash": device.bootstrap_key_hash,
                "api_key_hash": device.api_key_hash,
                "owner_user_id": device.owner_user_id,
                "plantation_id": device.plantation_id,
                "sector_id": device.sector_id,
                "is_provisioned": device.is_provisioned,
                "provisioned_at": device.provisioned_at,
                "revoked_at": device.revoked_at,
                "is_active": device.is_active,
                "created_at": device.created_at,
            },
        )

    @staticmethod
    def sync_registry_entry(entry: dict) -> bool:
        device_id = str(entry.get("iotDeviceMac") or entry.get("device_id") or "").strip()
        if not device_id:
            return False
        metadata = {
            "bootstrap_key_hash": str(
                entry.get("bootstrapCredentialHash") or entry.get("bootstrap_key_hash") or ""
            ).strip().lower(),
            "owner_user_id": int(entry.get("ownerUserId") or entry.get("owner_user_id") or 0),
            "plantation_id": int(entry.get("plantationId") or entry.get("plantation_id") or 0),
            "sector_id": int(entry.get("sectorId") or entry.get("sector_id") or 0),
            "is_active": bool(entry.get("isActive") if "isActive" in entry else entry.get("is_active", True)),
        }
        record = DeviceModel.get_or_none(DeviceModel.device_id == device_id)
        if record is None:
            DeviceModel.create(
                device_id=device_id,
                **metadata,
                is_provisioned=bool(entry.get("isProvisioned") if "isProvisioned" in entry else entry.get("is_provisioned", False)),
                api_key="",
            )
        else:
            # El estado operativo local no se sobreescribe con un registry
            # eventualmente atrasado del cloud; solo revoke/clear lo cambia.
            (DeviceModel
             .update(**metadata, api_key="")
             .where(DeviceModel.device_id == device_id)
             .execute())
        return True

    @staticmethod
    def provision(device_id: str, api_key_hash: str, provisioned_at: datetime) -> bool:
        updated = (DeviceModel
                   .update(
                       api_key_hash=api_key_hash,
                       is_provisioned=True,
                       provisioned_at=provisioned_at,
                       revoked_at=None,
                       api_key="",
                   )
                   .where(
                       (DeviceModel.device_id == device_id)
                       & (DeviceModel.is_provisioned == False)  # noqa: E712
                       & (DeviceModel.is_active == True)  # noqa: E712
                   )
                   .execute())
        return updated == 1

    @staticmethod
    def revoke(device_id: str, revoked_at: datetime) -> bool:
        updated = (DeviceModel
                   .update(
                       api_key_hash="",
                       is_provisioned=False,
                       provisioned_at=None,
                       revoked_at=revoked_at,
                   )
                   .where(DeviceModel.device_id == device_id)
                   .execute())
        return updated == 1

    @staticmethod
    def find_all() -> list[Device]:
        """
        Retorna todos los dispositivos registrados en la tabla `devices`.

        Returns:
            Lista de Device (vacía si no hay ninguno registrado).
        """
        return [
            _to_domain(record)
            for record in DeviceModel.select()
        ]
