"""
shared/infrastructure/database.py

Instancia compartida de SQLiteDatabase (Peewee) y función init_db().
Todos los modelos del proyecto importan `db` desde aquí para asociarse
a la misma base de datos.

init_db() recibe las clases de modelos como argumento para evitar
imports circulares: app.py importa los modelos primero y luego
llama a init_db(models).
"""

import logging
from peewee import SqliteDatabase

logger = logging.getLogger(__name__)

# Instancia única de la base de datos.
# La ruta se define en init_db(); hasta entonces permanece sin conectar.
db = SqliteDatabase(None)


def init_db(database_path: str, models: list) -> None:
    """
    Inicializa la conexión SQLite y crea las tablas si no existen.

    Args:
        database_path: Ruta al archivo .db (p.ej. "smart_palm.db").
        models:        Lista de clases Peewee Model a registrar
                       (DeviceModel, TelemetryRecordModel, AgronomicThresholdModel).
    """
    db.init(database_path)
    db.connect(reuse_if_open=True)
    db.create_tables(models, safe=True)
    _migrate_device_credentials()
    logger.info(
        "Base de datos inicializada: %s — tablas: %s",
        database_path,
        [m._meta.table_name for m in models],
    )


def _migrate_device_credentials() -> None:
    """Añade columnas de provisión a instalaciones SQLite anteriores.

    Peewee ``create_tables(safe=True)`` no altera tablas existentes. Esta
    migración es aditiva y permite reutilizar una base local de demostración
    sin volver a guardar API keys en texto plano.
    """
    if not db.table_exists("devices"):
        return
    columns = {row[1] for row in db.execute_sql("PRAGMA table_info(devices)").fetchall()}
    additions = {
        "bootstrap_key_hash": "TEXT NOT NULL DEFAULT ''",
        "api_key_hash": "TEXT NOT NULL DEFAULT ''",
        "owner_user_id": "INTEGER NOT NULL DEFAULT 0",
        "plantation_id": "INTEGER NOT NULL DEFAULT 0",
        "sector_id": "INTEGER NOT NULL DEFAULT 0",
        "is_provisioned": "INTEGER NOT NULL DEFAULT 0",
        "provisioned_at": "DATETIME NULL",
        "revoked_at": "DATETIME NULL",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute_sql(f'ALTER TABLE "devices" ADD COLUMN "{name}" {definition}')
