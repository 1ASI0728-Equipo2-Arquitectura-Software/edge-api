"""
iam/domain/model.py

Entidades del dominio IAM.
Device es el aggregate root del bounded context de identidad:
representa un dispositivo IoT registrado y autorizado para enviar
lecturas al Edge API.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone


def credential_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class Device:
    """Aggregate de credenciales de un dispositivo IoT."""

    device_id: str
    bootstrap_key_hash: str = ""
    api_key_hash: str = ""
    owner_user_id: int = 0
    plantation_id: int = 0
    sector_id: int = 0
    is_provisioned: bool = False
    provisioned_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = True
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def matches_credentials(self, device_id: str, api_key: str) -> bool:
        """
        Verifica si las credenciales proporcionadas coinciden con las del dispositivo.

        Args:
            device_id: Identificador del dispositivo a verificar.
            api_key:   API key a verificar.

        Returns:
            True si ambas credenciales coinciden, False en caso contrario.
        """
        if not self.is_active or not self.api_key_hash:
            return False
        return self.device_id == device_id and hmac.compare_digest(
            self.api_key_hash, credential_hash(api_key)
        )

    def matches_bootstrap(self, bootstrap_key: str) -> bool:
        if not self.is_active or self.is_provisioned or not self.bootstrap_key_hash:
            return False
        return hmac.compare_digest(
            self.bootstrap_key_hash, credential_hash(bootstrap_key)
        )
