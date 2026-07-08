"""
shared/infrastructure/discovery.py

Anuncio mDNS/zeroconf del Edge API en la red local.

Registra el servicio `_smartpalm._tcp.local.` con la IP y puerto donde este
Edge API escucha, para que el Edge Node (ESP32, Prototipo 2) lo descubra
automáticamente con `MDNS.queryService("smartpalm", "tcp")` sin necesidad de
configurar la IP de la laptop manualmente en cada red nueva.

Falla silenciosamente (mismo criterio que CloudApiClient): si zeroconf no
está instalado o la red bloquea multicast, el Edge API arranca igual y el
Edge Node puede fijar la URL manualmente vía POST /config/edge-api.

Variables de entorno:
  EDGE_ADVERTISE_IP — (opcional) IPv4 a anunciar. Útil cuando la laptop tiene
                      varias interfaces (Ethernet + WiFi) y la ruta por defecto
                      no es la interfaz que comparte red con el Edge Node.
"""

import atexit
import logging
import os
import socket

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_smartpalm._tcp.local."
_SERVICE_NAME = "edge-api._smartpalm._tcp.local."


def _primary_ipv4() -> str:
    """
    Determina la IPv4 de la interfaz con ruta por defecto (truco UDP-connect:
    no envía ningún paquete, solo resuelve qué interfaz usaría el SO).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def register_mdns_service(port: int = 5001) -> None:
    """
    Registra el servicio mDNS del Edge API. Idempotente por proceso;
    se desregistra automáticamente al terminar (atexit).

    Args:
        port: Puerto donde escucha el Edge API (default 5001).
    """
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        logger.warning(
            "Paquete 'zeroconf' no instalado — anuncio mDNS omitido. "
            "El Edge Node necesitará la URL manual (POST /config/edge-api). "
            "Instalar con: pip install zeroconf"
        )
        return

    try:
        ip = os.getenv("EDGE_ADVERTISE_IP", "").strip() or _primary_ipv4()
        info = ServiceInfo(
            _SERVICE_TYPE,
            _SERVICE_NAME,
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"path": "/api/v1/telemetry"},
        )
        zc = Zeroconf()
        zc.register_service(info)

        def _cleanup() -> None:
            try:
                zc.unregister_service(info)
                zc.close()
            except Exception:  # noqa: BLE001 — best effort al apagar
                pass

        atexit.register(_cleanup)
        logger.info("Servicio mDNS anunciado: %s → %s:%d", _SERVICE_NAME, ip, port)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "No se pudo anunciar el servicio mDNS (%s). El Edge Node puede "
            "fijar la URL manualmente vía POST /config/edge-api.",
            exc,
        )
