"""
app.py — Smart Palm Edge Service

Entrypoint del Edge API. Crea la aplicación Flask, registra los Blueprints
y ejecuta el bootstrap al iniciar la aplicación.

Bootstrap (ejecutado en create_app, antes de aceptar requests):
  1. Inicializa la base de datos SQLite y crea las tablas si no existen.
  2. Registra el Edge Device y el IoT Device en el Web Service central.
  3. Siembra el dispositivo de prueba (smart-palm-001) si no está registrado.
  4. Sincroniza umbrales agronómicos desde el Web Service central.

Variables de entorno:
  DATABASE_PATH   — Ruta al archivo SQLite (default: edge_data.db)
  CLOUD_BASE_URL  — URL base del Web Service (default: http://localhost:5000)
  EDGE_MAC        — Dirección MAC de este nodo edge registrada en el backend
                    (p.ej. AA:BB:CC:DD:EE:FF). Requerida para el envío de
                    telemetría al endpoint /api/v1/edge-gateways/{gatewayMac}/sensor-readings.
  IOT_MAC         — Dirección MAC del dispositivo IoT registrada en el backend
                    (p.ej. BB:CC:DD:EE:FF:AA). Requerida para la sincronización
                    de umbrales desde /api/v1/devices/{iotMac}/thresholds.
  CLOUD_API_KEY   — API key para el Web Service (default: vacío)
  CLOUD_TIMEOUT   — Timeout HTTP en segundos (default: 5)
"""

import logging
import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

from iam.application.services import AuthApplicationService
from iam.infrastructure.database import DeviceModel
from iam.infrastructure.repositories import DeviceRepository
from iam.interfaces.routes import iam_api
from shared.infrastructure.database import init_db
from shared.infrastructure.discovery import register_mdns_service
from telemetry.application.threshold_sync import ThresholdSyncService
from telemetry.infrastructure.cloud_client import CloudApiClient
from telemetry.infrastructure.database import (
    AgronomicThresholdModel,
    SensorReadingModel,
    TelemetryRecordModel,
)
from telemetry.infrastructure.repositories import AgronomicThresholdRepository
from telemetry.interfaces.routes import telemetry_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
_DATABASE_PATH = os.getenv("DATABASE_PATH", "edge_data.db")
_TEST_DEVICE_ID = "smart-palm-001"

# ---------------------------------------------------------------------------
# Modelos que init_db debe registrar (orden respeta FKs)
# ---------------------------------------------------------------------------
_DB_MODELS = [
    DeviceModel,
    TelemetryRecordModel,
    SensorReadingModel,
    AgronomicThresholdModel,
]

# ---------------------------------------------------------------------------
# Servicios de bootstrap (instanciados una vez)
# ---------------------------------------------------------------------------
_auth_service = AuthApplicationService(DeviceRepository())
_sync_service = ThresholdSyncService(AgronomicThresholdRepository())


def create_app() -> Flask:
    """
    Factoría de la aplicación Flask.

    Registra Blueprints y ejecuta el bootstrap antes de aceptar requests.

    Returns:
        Instancia de Flask lista para ejecutar.
    """
    app = Flask(__name__)

    # Registrar blueprints
    app.register_blueprint(iam_api)
    app.register_blueprint(telemetry_api)

    # Bootstrap síncrono: se ejecuta una vez al crear la app, no en el primer request.
    # Esto evita la condición de carrera cuando dos requests llegan simultáneamente
    # durante el arranque.
    logger.info("Iniciando bootstrap del Edge API...")

    # 1. Inicializar base de datos
    init_db(_DATABASE_PATH, _DB_MODELS)

    # 1.5 Anunciar este Edge API por mDNS (_smartpalm._tcp) para que el
    #     Edge Node (ESP32) descubra la IP de esta máquina en cualquier red.
    #     Falla silenciosa si zeroconf no está disponible.
    register_mdns_service(port=5001)

    # 2. Registrar dispositivos en el cloud (idempotente: 409 = ya registrado)
    edge_registered = CloudApiClient.register_edge_device()
    if edge_registered:
        CloudApiClient.register_iot_device()
    else:
        logger.warning(
            "No se pudo registrar el Edge Device en el cloud — "
            "IoT Device y umbrales no se sincronizarán."
        )

    # 2.5 Sincronizar lista de IoT devices autorizados desde el Cloud al IAM local
    mac_list = CloudApiClient.fetch_registry()
    new_count = _auth_service.sync_from_registry(mac_list)
    if new_count > 0:
        logger.info("Registry sincronizado: %d device(s) nuevo(s) añadidos al IAM.", new_count)
    elif mac_list:
        logger.info("Registry sincronizado: todos los devices ya estaban en el IAM local.")
    else:
        logger.info("Registry vacío o no disponible — IAM local sin cambios.")

    # 3. Sembrar dispositivo de prueba en IAM local
    _auth_service.seed_test_device()

    # 4. Sincronizar umbrales desde el cloud
    synced = _sync_service.sync(_TEST_DEVICE_ID)
    if synced == 0:
        logger.info(
            "Sin umbrales del cloud — se usarán valores agronómicos por defecto."
        )

    logger.info("Bootstrap completado.")

    return app


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5001, debug=debug_mode)
