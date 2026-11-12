import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from iam.infrastructure.database import DeviceModel
from iam.infrastructure.repositories import DeviceRepository
from shared.infrastructure.database import (
    backfill_legacy_telemetry_owner,
    db,
    init_db,
)
from telemetry.application.services import TelemetryApplicationService
from telemetry.domain.model import SensorReadingValue, TelemetryRecord
from telemetry.infrastructure.database import (
    AgronomicThresholdModel,
    CloudOutboxModel,
    SensorReadingModel,
    TelemetryRecordModel,
)
from telemetry.infrastructure.repositories import (
    CloudOutboxRepository,
    TelemetryRepository,
)
from telemetry.interfaces.routes import telemetry_api
from flask import Flask


MODELS = [
    DeviceModel,
    TelemetryRecordModel,
    SensorReadingModel,
    AgronomicThresholdModel,
    CloudOutboxModel,
]


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        init_db(self.path, MODELS)

    def tearDown(self):
        if not db.is_closed():
            db.close()
        for path in (self.path, self.path + ".bak"):
            if os.path.exists(path):
                os.remove(path)

    def test_schema_migration_is_additive_and_backfill_is_validated(self):
        for table in ("telemetry_records", "sensor_readings"):
            columns = {
                row[1]
                for row in db.execute_sql(f"PRAGMA table_info({table})").fetchall()
            }
            self.assertIn("owner_user_id", columns)

        record = TelemetryRecordModel.create(device_id="D1")
        SensorReadingModel.create(
            record=record,
            variable="temperature",
            value=25,
            unit="C",
            timestamp=datetime.now(timezone.utc),
        )
        counts = {"devices": 0, "telemetry_records": 1, "sensor_readings": 1}
        backfill_legacy_telemetry_owner(11, self.path + ".bak", counts)
        self.assertEqual(TelemetryRecordModel.get_by_id(record.id).owner_user_id, 11)
        self.assertEqual(SensorReadingModel.get().owner_user_id, 11)

    def test_registry_owner_is_copied_to_all_telemetry_and_exported(self):
        DeviceRepository.sync_registry_entry(
            {"iotDeviceMac": "D1", "ownerUserId": 11, "isActive": True}
        )

        class NoThresholds:
            @staticmethod
            def find_by_variable(variable):
                return None

        service = TelemetryApplicationService(TelemetryRepository(), NoThresholds())
        record_id, _ = service.record_telemetry(
            "D1",
            [
                {
                    "variable": "temperature",
                    "value": 25,
                    "unit": "C",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            ],
        )
        self.assertEqual(TelemetryRecordModel.get_by_id(record_id).owner_user_id, 11)
        self.assertEqual(SensorReadingModel.get().owner_user_id, 11)
        self.assertEqual(len(TelemetryRepository.export_by_owner_user_id(11)), 1)
        self.assertEqual(CloudOutboxRepository.to_domain(CloudOutboxRepository.due()[0]).owner_user_id, 11)

    def test_export_requires_scoped_internal_identity(self):
        app = Flask(__name__)
        app.register_blueprint(telemetry_api)
        with patch.dict(
            os.environ,
            {"EDGE_NODE_KEY": "internal-secret", "EDGE_EXPORT_OWNER_USER_ID": "11"},
            clear=False,
        ):
            with app.test_client() as client:
                self.assertEqual(client.get("/api/v1/telemetry/export").status_code, 401)
                response = client.get(
                    "/api/v1/telemetry/export",
                    headers={"X-Edge-Node-Key": "internal-secret"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["owner_user_id"], 11)
                self.assertEqual(
                    client.get(
                        "/api/v1/telemetry/export?owner_user_id=12",
                        headers={"X-Edge-Node-Key": "internal-secret"},
                    ).status_code,
                    400,
                )


if __name__ == "__main__":
    unittest.main()
