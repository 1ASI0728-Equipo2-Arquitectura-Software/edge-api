"""
iam/interfaces/routes.py

Blueprint Flask para el endpoint de autenticación del bounded context IAM.

Expone:
  GET /api/v1/auth/verify
    Headers: X-Device-Id  — identificador del dispositivo
             X-Api-Key    — clave de autenticación del dispositivo
    200:     { "device_id": "<id>", "status": "authenticated" }
    401:     { "error": "Unauthorized" }

Permite al ESP32 verificar que sus credenciales son válidas y que el
Edge API está disponible, sin necesidad de enviar un lote de telemetría.
Útil en el ciclo de arranque del dispositivo embebido.
"""

import logging
import hmac
import os

from flask import Blueprint, jsonify, request

from iam.application.services import AuthApplicationService
from iam.infrastructure.repositories import DeviceRepository
from telemetry.infrastructure.cloud_client import CloudApiClient

_device_repo = DeviceRepository()

logger = logging.getLogger(__name__)

iam_api = Blueprint("iam_api", __name__)

_auth_service = AuthApplicationService(DeviceRepository())
def _management_key_is_valid() -> bool:
    expected = os.getenv("EDGE_NODE_KEY", "").strip()
    supplied = request.headers.get("X-Edge-Node-Key", "")
    return bool(expected) and hmac.compare_digest(supplied, expected)


@iam_api.get("/api/v1/auth/verify")
def verify_credentials():
    """
    Verifica las credenciales de un dispositivo IoT.

    El ESP32 puede llamar a este endpoint al arrancar para confirmar
    que sus credenciales son válidas antes de iniciar el envío de datos.

    Returns:
        200: Credenciales válidas.
             { "device_id": "<id>", "status": "authenticated" }
        401: Credenciales ausentes o incorrectas.
             { "error": "Unauthorized" }
    """
    device_id = request.headers.get("X-Device-Id", "").strip()
    api_key = request.headers.get("X-Api-Key", "").strip()

    if not device_id or not api_key:
        return jsonify({"error": "Unauthorized"}), 401

    if not _auth_service.authenticate(device_id, api_key):
        logger.warning(
            "Verificación de credenciales fallida: device_id='%s'.", device_id
        )
        return jsonify({"error": "Unauthorized"}), 401

    logger.info("Credenciales verificadas: device_id='%s'.", device_id)
    return jsonify({"device_id": device_id, "status": "authenticated"}), 200


@iam_api.get("/api/v1/authorized-devices")
def get_authorized_devices():
    """
    Retorna la lista de IoT Devices autorizados almacenados en el IAM local.

    Endpoint interno consumido por el Edge Node (ESP32) al arrancar para
    cargar metadatos de los dispositivos autorizados. Nunca devuelve una
    API key ni el bootstrap.

    Returns:
        200: Lista de metadatos sin secretos.
    """
    if not _management_key_is_valid():
        return jsonify({"error": "Unauthorized"}), 401

    devices = _device_repo.find_all()
    payload = [
        {
            "device_id": d.device_id,
            "owner_user_id": d.owner_user_id,
            "plantation_id": d.plantation_id,
            "sector_id": d.sector_id,
            "is_provisioned": d.is_provisioned,
            "is_active": d.is_active,
        }
        for d in devices
    ]
    logger.info("Edge Node consultó authorized-devices: %d device(s) retornados.", len(payload))
    return jsonify(payload), 200


@iam_api.post("/api/v1/provisioning/devices/<path:device_id>")
def provision_device(device_id: str):
    """Enrola una vez un sensor usando su bootstrap de preparación física."""
    if not _management_key_is_valid():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    bootstrap_key = str(body.get("bootstrapKey") or body.get("bootstrap_key") or "").strip()
    if not bootstrap_key:
        return jsonify({"error": "bootstrapKey es requerido."}), 400

    try:
        api_key, provisioned_at = _auth_service.provision(device_id.strip(), bootstrap_key)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409

    CloudApiClient.mark_device_provisioned(device_id.strip(), True)
    # api_key solo existe en esta respuesta y no se persiste en texto plano.
    return jsonify({
        "device_id": device_id,
        "api_key": api_key,
        "provisioned_at": provisioned_at.isoformat(),
    }), 201


@iam_api.post("/api/v1/provisioning/devices/<path:device_id>/revoke")
def revoke_device_provisioning(device_id: str):
    """Limpieza explícita para una reprovisión controlada de demostración."""
    if not _management_key_is_valid():
        return jsonify({"error": "Unauthorized"}), 401
    if not _auth_service.revoke(device_id.strip()):
        return jsonify({"error": "Dispositivo no registrado."}), 404
    CloudApiClient.mark_device_provisioned(device_id.strip(), False)
    return jsonify({"device_id": device_id, "status": "revoked"}), 200
