"""
iam/infrastructure/database.py

Modelo Peewee para la tabla `devices`.
DeviceModel es la representación de infraestructura de la entidad Device.
"""

from datetime import datetime, timezone
from peewee import Model, CharField, DateTimeField, IntegerField, BooleanField

from shared.infrastructure.database import db


class DeviceModel(Model):
    """
    Tabla `devices` en SQLite.

    Columns:
        device_id:  Identificador único del dispositivo (PK lógica).
        api_key:    Clave de autenticación del dispositivo.
        created_at: Fecha y hora UTC de registro.
    """

    device_id = CharField(max_length=100, unique=True, index=True)
    # Legacy column retained only so existing SQLite files can migrate safely.
    # New code never writes or returns this value.
    api_key = CharField(max_length=255, default="")
    bootstrap_key_hash = CharField(max_length=128, default="")
    api_key_hash = CharField(max_length=128, default="")
    owner_user_id = IntegerField(default=0)
    plantation_id = IntegerField(default=0)
    sector_id = IntegerField(default=0)
    is_provisioned = BooleanField(default=False)
    provisioned_at = DateTimeField(null=True)
    revoked_at = DateTimeField(null=True)
    is_active = BooleanField(default=True)
    created_at = DateTimeField(
        default=lambda: datetime.now(timezone.utc)
    )

    class Meta:
        database = db
        table_name = "devices"
