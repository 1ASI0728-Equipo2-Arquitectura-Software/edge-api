"""
telemetry/infrastructure/cloud_client.py

Cliente HTTP hacia el Web Service central (SmartPalmPlatform.API).

CloudApiClient encapsula toda la comunicación saliente del Edge API
con la nube. Falla silenciosamente: si el cloud no está disponible,
los métodos retornan valores seguros en lugar de propagar excepciones,
permitiendo que el Edge API opere en modo offline.

Endpoints del Web Service utilizados:
  GET  /api/v1/devices/{iotMac}/thresholds
         — Obtiene el umbral agronómico configurado para el dispositivo IoT.
         — Respuesta: [{ "edgeMac", "iotMac", "min", "max", "description", "type" }, ...]
         — 404 si aún no hay umbral configurado para ese dispositivo.
  POST /api/v1/edge-gateways/{gatewayMac}/sensor-readings
         — Envía el lote de lecturas agrupado por dispositivo IoT.
         — Respuesta: 201 Created (sin cuerpo).

Payload esperado por el endpoint /sensor-readings:
  {
      "devices": [
          {
              "deviceMac": "...",
              "readings": [
                  {"sensorType": "Temperature",  "measuredAt": "...", "value": 25.5},
                  {"sensorType": "Humidity",     "measuredAt": "...", "value": 65.0},
                  {"sensorType": "SoilMoisture", "measuredAt": "...", "value": 45.0},
                  {"sensorType": "Luminosity",   "measuredAt": "...", "value": 78.0}
              ]
          }
      ],
      "syncedAt": "..."
  }

Configuración (variables de entorno):
  CLOUD_BASE_URL  — URL base del Web Service, sin slash final.
                    Por defecto: http://localhost:5000
  EDGE_MAC        — Dirección MAC de este nodo edge tal como fue registrada
                    en el backend (p.ej. AA:BB:CC:DD:EE:FF).
                    Requerida para construir las rutas de ambos endpoints.
  IOT_MAC         — Dirección MAC del dispositivo IoT asociado, registrada
                    en el backend (p.ej. BB:CC:DD:EE:FF:AA).
                    Requerida únicamente para el endpoint de umbrales.
  CLOUD_API_KEY   — API key opcional para autenticación con el Web Service.
                    Por defecto: vacío (sin autenticación en desarrollo).
  CLOUD_TIMEOUT   — Timeout en segundos para cada request.
                    Por defecto: 5
"""

import logging
import os
import time
from urllib.parse import quote
from typing import Optional

import requests

from telemetry.domain.model import AgronomicThreshold, TelemetryRecord

logger = logging.getLogger(__name__)

_BASE_URL       = os.getenv("CLOUD_BASE_URL", "http://localhost:5000")
_EDGE_MAC       = os.getenv("EDGE_MAC", "")
_IOT_MAC        = os.getenv("IOT_MAC", "")
_API_KEY        = os.getenv("CLOUD_API_KEY", "")
_GATEWAY_KEY    = os.getenv("CLOUD_GATEWAY_KEY", "")
_TIMEOUT        = int(os.getenv("CLOUD_TIMEOUT", "5"))
_ADMIN_USERNAME = os.getenv("CLOUD_ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = os.getenv("CLOUD_ADMIN_PASSWORD", "admin")

# ─── Sesión JWT con el Web Service ──────────────────────────────────────────
# El backend protege /edge-gateways, /devices/{mac}/thresholds y el registry
# con [Authorize]. El Edge API se autentica una vez con las credenciales de
# administrador y reutiliza el token hasta que esté por vencer.
# ───────────────────────────────────────────────────────────────────────────
_TOKEN_TTL_SECONDS = 50 * 60  # se refresca antes de que el JWT expire (~1h)
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _safe_response_message(response: requests.Response) -> str:
    """Extracts a bounded server message without exposing credentials or tokens."""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("title") or payload.get("detail")
            if message:
                return str(message)[:500]
    except ValueError:
        pass

    text = response.text.strip()
    return text[:500] if text else "empty response body"


def _get_auth_token() -> Optional[str]:
    """Obtiene (y cachea) un JWT autenticándose como administrador en el cloud."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    if not _ADMIN_USERNAME or not _ADMIN_PASSWORD:
        logger.debug("CLOUD_ADMIN_USERNAME/PASSWORD no configurados — sin token de cloud.")
        return None

    url = f"{_BASE_URL}/api/v1/authentication/sign-in"
    try:
        response = requests.post(
            url,
            json={"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            logger.warning("Sign-in al cloud no devolvió token.")
            return None
        _token_cache["token"] = token
        _token_cache["expires_at"] = time.time() + _TOKEN_TTL_SECONDS
        logger.info("Autenticado en el cloud como '%s'.", _ADMIN_USERNAME)
        return token
    except requests.exceptions.HTTPError as exc:
        logger.warning(
            "Error HTTP %s al autenticarse en el cloud: %s.", exc.response.status_code, exc
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo autenticar en el cloud: %s.", exc)
    return None

# ─── Mapeo bidireccional entre nombres de variable del Edge API ─────────────
# y SensorType del Web Service.
# Edge API usa camelCase (p.ej. "soilMoisture"); backend usa PascalCase
# (p.ej. "SoilMoisture"), con la excepción de "light" → "Luminosity".
# ───────────────────────────────────────────────────────────────────────────
_VARIABLE_TO_SENSOR_TYPE: dict[str, str] = {
    "temperature":  "Temperature",
    "humidity":     "Humidity",
    "soilMoisture": "SoilMoisture",
    "light":        "Luminosity",
}

_SENSOR_TYPE_TO_VARIABLE: dict[str, str] = {
    v: k for k, v in _VARIABLE_TO_SENSOR_TYPE.items()
}


def _to_sensor_type(variable: str) -> str:
    """Convierte el nombre de variable del Edge API al SensorType del backend."""
    return _VARIABLE_TO_SENSOR_TYPE.get(variable, variable)


def _to_variable(sensor_type: str) -> str:
    """Convierte el SensorType del backend al nombre de variable del Edge API."""
    return _SENSOR_TYPE_TO_VARIABLE.get(sensor_type, sensor_type.lower())


class CloudApiClient:
    """
    Cliente HTTP para comunicación con el Web Service central.

    Todos los métodos capturan excepciones de red y retornan valores
    seguros (lista vacía, False) para garantizar que el Edge API nunca
    falle por indisponibilidad del cloud.
    """

    @staticmethod
    def _headers(include_gateway_key: bool = False) -> dict:
        """Construye los headers HTTP comunes para todas las peticiones."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if _API_KEY:
            headers["X-Api-Key"] = _API_KEY
        if include_gateway_key and _GATEWAY_KEY:
            headers["X-Gateway-Key"] = _GATEWAY_KEY
        token = _get_auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def get_thresholds(device_id: str) -> list[AgronomicThreshold]:
        """
        Obtiene el umbral agronómico configurado para el dispositivo IoT.

        Hace GET a /api/v1/devices/{iotMac}/thresholds.
        Si el cloud no responde o retorna un error, retorna lista vacía
        para que TelemetryDomainService use los valores por defecto.

        El backend identifica al dispositivo por su MAC address (IOT_MAC),
        no por el device_id interno del Edge API. El parámetro device_id
        se conserva para compatibilidad con ThresholdSyncService pero no se
        utiliza en la construcción de la URL.

        Args:
            device_id: Identificador interno del dispositivo (no utilizado
                       en la ruta; la identificación en el cloud se realiza
                       mediante la variable de entorno IOT_MAC).

        Returns:
            Lista con un AgronomicThreshold sincronizado desde el cloud,
            o lista vacía si el cloud no está disponible o la variable
            de entorno no está configurada.
        """
        if not _IOT_MAC:
            logger.debug(
                "IOT_MAC no configurado — sincronización de umbrales omitida."
            )
            return []

        url = f"{_BASE_URL}/api/v1/devices/{_IOT_MAC}/thresholds"
        try:
            response = requests.get(
                url,
                headers=CloudApiClient._headers(),
                timeout=_TIMEOUT,
            )
            response.raise_for_status()

            data = response.json()

            # El backend devuelve un objeto único AgronomicThresholdViewResource:
            # { "edgeMac", "iotMac", "min", "max", "description", "type" }
            # Normalizar a lista para procesar uniformemente.
            if isinstance(data, dict):
                data = [data]

            if not isinstance(data, list) or len(data) == 0:
                logger.info("Sin umbrales configurados en el cloud para este par edge/iot.")
                return []

            thresholds = []
            for item in data:
                threshold = AgronomicThreshold(
                    variable=_to_variable(item.get("type", "")),
                    min_value=float(item.get("min", 0.0)),
                    max_value=float(item.get("max", 100.0)),
                )
                logger.info(
                    "Umbral obtenido del cloud: variable='%s' [%.1f, %.1f].",
                    threshold.variable,
                    threshold.min_value,
                    threshold.max_value,
                )
                thresholds.append(threshold)
            return thresholds

        except requests.exceptions.ConnectionError:
            logger.warning(
                "Cloud no disponible al obtener umbrales (ConnectionError). "
                "Se usarán umbrales por defecto."
            )
        except requests.exceptions.Timeout:
            logger.warning(
                "Timeout al obtener umbrales del cloud (>%ds). "
                "Se usarán umbrales por defecto.",
                _TIMEOUT,
            )
        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 404:
                # 404 es estado normal: aún no hay umbral configurado en el backend.
                logger.info(
                    "Sin umbral configurado en el cloud para iot=%s (404).",
                    _IOT_MAC,
                )
            else:
                logger.warning(
                    "Error HTTP %s al obtener umbrales del cloud: %s.",
                    exc.response.status_code,
                    _safe_response_message(exc.response),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error inesperado al obtener umbrales del cloud: %s.", exc)

        return []

    @staticmethod
    def post_telemetry_batch(record: TelemetryRecord) -> bool:
        """
        Reenvía un lote de lecturas al Web Service central.

        Hace POST a /api/v1/edge-gateways/{edgeMac}/sensor-readings con el
        payload agrupado por dispositivo IoT esperado por el backend.
        Si el cloud no responde, retorna False sin interrumpir el flujo
        principal del Edge API (el lote ya fue persistido localmente).

        Los nombres de variable del Edge API (p.ej. "soilMoisture") se
        convierten automáticamente al SensorType del backend (p.ej.
        "SoilMoisture") mediante el mapeo _VARIABLE_TO_SENSOR_TYPE.

        Args:
            record: TelemetryRecord con las lecturas a reenviar.

        Returns:
            True si el cloud respondió con HTTP 200 o 201, False si falló.
        """
        if not _EDGE_MAC:
            logger.debug("EDGE_MAC no configurado — reenvío al cloud omitido.")
            return False

        url = f"{_BASE_URL}/api/v1/edge-gateways/{_EDGE_MAC}/sensor-readings"
        timestamp = record.recorded_at.isoformat()

        payload = {
            "devices": [
                {
                    "deviceMac": record.device_id,
                    "readings": [
                        {
                            "sensorType": _to_sensor_type(r.variable),
                            "measuredAt": r.timestamp.isoformat(),
                            "value": r.value,
                        }
                        for r in record.readings
                    ],
                }
            ],
            "syncedAt": timestamp,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=CloudApiClient._headers(include_gateway_key=True),
                timeout=_TIMEOUT,
            )
            response.raise_for_status()

            logger.info(
                "Lote reenviado al cloud: device_id='%s', edge='%s', lecturas=%d. HTTP %d.",
                record.device_id,
                _EDGE_MAC,
                len(record.readings),
                response.status_code,
            )
            return True

        except requests.exceptions.ConnectionError:
            logger.warning(
                "Cloud no disponible al reenviar lote (ConnectionError). "
                "El lote quedó persistido localmente."
            )
        except requests.exceptions.Timeout:
            logger.warning(
                "Timeout al reenviar lote al cloud (>%ds). "
                "El lote quedó persistido localmente.",
                _TIMEOUT,
            )
        except requests.exceptions.HTTPError as exc:
            logger.warning(
                "Error HTTP %s al reenviar lote al cloud: %s.",
                exc.response.status_code,
                _safe_response_message(exc.response),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error inesperado al reenviar lote al cloud: %s.", exc)

        return False

    @staticmethod
    def mark_device_provisioned(device_id: str, provisioned: bool) -> bool:
        """Replica en DeviceService el estado de provisión local del Edge API."""
        action = "provision" if provisioned else "revoke"
        url = f"{_BASE_URL}/api/v1/admin/iot-devices/{quote(device_id, safe='')}/{action}"
        try:
            response = requests.post(url, headers=CloudApiClient._headers(), timeout=_TIMEOUT)
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No se pudo sincronizar provisión de '%s' con el cloud: %s.",
                device_id,
                exc,
            )
            return False

    @staticmethod
    def fetch_registry() -> list[dict]:
        """
        Obtiene la lista de IoT Devices autorizados para este Edge desde el Cloud.

        Hace GET a /api/v1/edge-gateways/{edgeMac}/registry y extrae los iotDeviceMac
        del campo `registry`. Si el Cloud no responde o el Edge no está aún
        registrado (404), retorna lista vacía y el IAM local usará lo que ya
        tenga en SQLite de sincronizaciones anteriores.

        Returns:
            Lista de MAC address strings de los IoT Devices autorizados,
            o lista vacía si el Cloud no está disponible.
        """
        if not _EDGE_MAC:
            logger.debug("EDGE_MAC no configurado — fetch de registry omitido.")
            return []

        url = f"{_BASE_URL}/api/v1/edge-gateways/{_EDGE_MAC}/registry"
        try:
            response = requests.get(
                url,
                headers=CloudApiClient._headers(),
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            registry = [entry for entry in data.get("registry", []) if entry.get("iotDeviceMac")]
            logger.info(
                "Registry obtenido del cloud: %d IoT device(s) autorizados.", len(registry)
            )
            return registry

        except requests.exceptions.ConnectionError:
            logger.warning("Cloud no disponible al obtener registry (ConnectionError).")
        except requests.exceptions.Timeout:
            logger.warning("Timeout al obtener registry del cloud (>%ds).", _TIMEOUT)
        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 404:
                logger.info(
                    "Edge Device '%s' aún no registrado en el cloud — registry vacío (404).",
                    _EDGE_MAC,
                )
            else:
                logger.warning(
                    "Error HTTP %s al obtener registry: %s.", exc.response.status_code, exc
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error inesperado al obtener registry del cloud: %s.", exc)

        return []

