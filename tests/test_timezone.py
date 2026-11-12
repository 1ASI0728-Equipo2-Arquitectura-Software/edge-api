import unittest
from datetime import datetime, timezone

from telemetry.application.services import TelemetryApplicationService
from telemetry.domain.time import to_presentation_timezone, to_utc_iso


class TimezoneBehaviorTests(unittest.TestCase):
    def test_offsets_are_normalized_to_utc(self):
        value = datetime.fromisoformat("2026-11-12T15:00:00-05:00")

        self.assertEqual(to_utc_iso(value), "2026-11-12T20:00:00+00:00")

    def test_presentation_conversion_uses_bogota_without_changing_instant(self):
        value = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)

        presented = to_presentation_timezone(value)

        self.assertEqual(presented.hour, 15)
        self.assertEqual(presented.utcoffset().total_seconds(), -5 * 3600)

    def test_invalid_sensor_timestamp_uses_one_reception_instant(self):
        received_at = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)
        readings = TelemetryApplicationService._parse_readings(
            [
                {"variable": "temperature", "value": 20, "unit": "C", "timestamp": "bad"},
                {"variable": "humidity", "value": 50, "unit": "%", "timestamp": "UPTIME+1s"},
            ],
            received_at,
        )

        self.assertEqual([reading.timestamp for reading in readings], [received_at, received_at])


if __name__ == "__main__":
    unittest.main()
