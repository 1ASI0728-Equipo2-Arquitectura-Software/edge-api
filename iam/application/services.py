"""
iam/application/services.py

Application service del bounded context IAM.

AuthApplicationService orquesta la autenticación de dispositivos IoT.

Esta capa no contiene lógica de negocio; delega en DeviceDomainService
y en el repositorio para persistencia.
"""

import logging
import secrets
from datetime import datetime, timezone

from iam.domain.model import credential_hash
from iam.domain.services import DeviceDomainService, IDeviceRepository

logger = logging.getLogger(__name__)



class AuthApplicationService:
    """
    Orquestador de casos de uso de autenticación.

    Attributes:
        _domain_service: Lógica de negocio de autenticación de dispositivos.
        _repo:           Repositorio para persistencia de dispositivos.
    """

    def __init__(self, device_repository: IDeviceRepository) -> None:
        self._repo = device_repository
        self._domain_service = DeviceDomainService(device_repository)

    def authenticate(self, device_id: str, api_key: str) -> bool:
        """
        Valida las credenciales de un dispositivo IoT.

        Args:
            device_id: Identificador del dispositivo (header X-Device-Id).
            api_key:   Clave de autenticación (header X-Api-Key).

        Returns:
            True si las credenciales son válidas, False en caso contrario.
        """
        result = self._domain_service.authenticate(device_id, api_key)
        if not result:
            logger.warning(
                "Autenticación fallida para device_id='%s'", device_id
            )
        return result

    def sync_from_registry(self, registry: list[dict | str]) -> int:
        """
        Sincroniza metadatos y el hash de bootstrap sin crear API keys.

        Args:
            registry: Entradas completas obtenidas de GET /registry en Cloud.

        Returns:
            Número de dispositivos nuevos añadidos al IAM local.
        """
        new_count = 0
        for raw_entry in registry:
            entry = {"iotDeviceMac": raw_entry} if isinstance(raw_entry, str) else raw_entry
            device_id = str(entry.get("iotDeviceMac") or entry.get("device_id") or "").strip()
            if not device_id:
                continue
            existed = self._repo.find_by_device_id(device_id) is not None
            if self._repo.sync_registry_entry(entry):
                new_count += 0 if existed else 1
                logger.info("Registry sincronizado: device_id='%s'.", device_id)
        return new_count

    def provision(self, device_id: str, bootstrap_key: str) -> tuple[str, datetime]:
        device = self._repo.find_by_device_id(device_id)
        if device is None:
            raise LookupError("Dispositivo no registrado en el Edge API.")
        if not device.is_active:
            raise PermissionError("Dispositivo inactivo.")
        if device.is_provisioned:
            raise RuntimeError("El bootstrap ya fue utilizado; se requiere revocación explícita.")
        if not device.matches_bootstrap(bootstrap_key):
            raise PermissionError("Bootstrap inválido.")

        api_key = secrets.token_urlsafe(32)
        provisioned_at = datetime.now(timezone.utc)
        if not self._repo.provision(device_id, credential_hash(api_key), provisioned_at):
            raise RuntimeError("El dispositivo fue provisionado por otra solicitud.")
        return api_key, provisioned_at

    def revoke(self, device_id: str) -> bool:
        return self._repo.revoke(device_id, datetime.now(timezone.utc))
