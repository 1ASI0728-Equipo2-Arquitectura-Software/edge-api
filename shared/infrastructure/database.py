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
import os
import sqlite3
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
    _migrate_telemetry_ownership()
    _maybe_backfill_legacy_telemetry()
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


def _migrate_telemetry_ownership() -> None:
    """Add ownership columns without recreating or dropping existing tables."""
    additions = {
        "telemetry_records": "owner_user_id",
        "sensor_readings": "owner_user_id",
    }
    for table, column in additions.items():
        if not db.table_exists(table):
            continue
        columns = {
            row[1]
            for row in db.execute_sql(f'PRAGMA table_info("{table}")').fetchall()
        }
        if column not in columns:
            db.execute_sql(
                f'ALTER TABLE "{table}" ADD COLUMN "{column}" INTEGER NOT NULL DEFAULT 0'
            )


def backfill_legacy_telemetry_owner(
    owner_user_id: int,
    backup_path: str,
    expected_counts: dict[str, int] | None = None,
) -> dict[str, int]:
    """Backfill unowned legacy telemetry after making and validating a backup."""
    if owner_user_id <= 0:
        raise ValueError("owner_user_id must be positive")
    if not backup_path:
        raise ValueError("backup_path is required for legacy backfill")
    if os.path.exists(backup_path):
        raise FileExistsError(f"Backup already exists: {backup_path}")

    tables = ("devices", "telemetry_records", "sensor_readings")
    counts = {
        table: db.execute_sql(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
        for table in tables
        if db.table_exists(table)
    }
    if expected_counts is not None and counts != expected_counts:
        raise ValueError(
            f"Legacy backfill count validation failed: expected {expected_counts}, got {counts}"
        )

    source = sqlite3.connect(db.database)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    if not os.path.exists(backup_path) or os.path.getsize(backup_path) == 0:
        raise RuntimeError("Legacy backfill backup validation failed")

    with db.atomic():
        for table in ("telemetry_records", "sensor_readings"):
            if db.table_exists(table):
                db.execute_sql(
                    f'UPDATE "{table}" SET owner_user_id = ? WHERE owner_user_id = 0',
                    (owner_user_id,),
                )
    return counts


def _maybe_backfill_legacy_telemetry() -> None:
    """Run only when explicitly configured and a validated backup is requested."""
    configured_user = (
        os.getenv("LEGACY_BACKFILL_OWNER_USER_ID")
        or os.getenv("LEGACY_TELEMETRY_BACKFILL_USER_ID")
        or ""
    ).strip()
    backup_path = (
        os.getenv("LEGACY_BACKFILL_BACKUP_PATH")
        or os.getenv("LEGACY_TELEMETRY_BACKFILL_BACKUP_PATH")
        or ""
    ).strip()
    if not configured_user or not backup_path:
        return
    expected_raw = os.getenv("LEGACY_BACKFILL_EXPECTED_COUNTS", "").strip()
    expected = None
    if expected_raw:
        import json
        expected = {key: int(value) for key, value in json.loads(expected_raw).items()}
    backfill_legacy_telemetry_owner(int(configured_user), backup_path, expected)
