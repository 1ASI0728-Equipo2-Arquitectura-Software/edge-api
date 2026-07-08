"""
iam/application/services.py

Application service del bounded context IAM.

AuthApplicationService orquesta los casos de uso de autenticación:
- authenticate: valida credenciales de un dispositivo IoT.
- seed_test_device: siembra un dispositivo de prueba en bootstrap.

Esta capa no contiene lógica de negocio; delega en DeviceDomainService
y en el repositorio para persistencia.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from iam.domain.model import Device
from iam.domain.services import DeviceDomainService, IDeviceRepository

logger = logging.getLogger(__name__)

_TEST_DEVICE_ID = "smart-palm-001"
_TEST_API_KEY = "sp-test-api-key-123"


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

    def seed_test_device(self) -> None:
        """
        Siembra el dispositivo de prueba si aún no existe en la base de datos.

        Llamado una sola vez durante el bootstrap de la aplicación (before_request).
        Garantiza que el ESP32 pueda autenticarse con credenciales conocidas en
        entornos de desarrollo y prueba.
        """
        existing = self._repo.find_by_device_id(_TEST_DEVICE_ID)
        if existing is not None:
            logger.debug(
                "Dispositivo de prueba '%s' ya existe, seed omitido.",
                _TEST_DEVICE_ID,
            )
            return

        test_device = Device(
            device_id=_TEST_DEVICE_ID,
            api_key=_TEST_API_KEY,
            created_at=datetime.now(timezone.utc),
        )
        self._repo.save(test_device)
        logger.info(
            "Dispositivo de prueba sembrado: device_id='%s'", _TEST_DEVICE_ID
        )

    def sync_from_registry(self, mac_list: list[str]) -> int:
        """
        Sincroniza el IAM local con la lista de MACs autorizadas por el Cloud.

        Por cada MAC recibida del registry del Cloud, si el dispositivo no
        existe aún en SQLite, genera un api_key UUID y lo persiste. Dispositivos
        ya registrados conservan su api_key existente (idempotente en reinicios).

        Args:
            mac_list: Lista de MAC addresses obtenidas de GET /registry en Cloud.

        Returns:
            Número de dispositivos nuevos añadidos al IAM local.
        """
        new_count = 0
        for mac in mac_list:
            if self._repo.find_by_device_id(mac) is None:
                device = Device(
                    device_id=mac,
                    api_key=str(uuid4()),
                    created_at=datetime.now(timezone.utc),
                )
                self._repo.save(device)
                logger.info("Dispositivo registrado en IAM local: device_id='%s'.", mac)
                new_count += 1
        return new_count
