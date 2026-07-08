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

from flask import Blueprint, jsonify, request

from iam.application.services import AuthApplicationService
from iam.infrastructure.repositories import DeviceRepository

_device_repo = DeviceRepository()

logger = logging.getLogger(__name__)

iam_api = Blueprint("iam_api", __name__)

_auth_service = AuthApplicationService(DeviceRepository())


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
    cargar en memoria la lista de dispositivos que puede autenticar.
    No requiere autenticación ya que solo es accesible desde la red local.

    Returns:
        200: Lista de dispositivos con su api_key.
             [ { "device_id": "<mac>", "api_key": "<key>" }, ... ]
    """
    devices = _device_repo.find_all()
    payload = [
        {"device_id": d.device_id, "api_key": d.api_key}
        for d in devices
    ]
    logger.info("Edge Node consultó authorized-devices: %d device(s) retornados.", len(payload))
    return jsonify(payload), 200
