"""In-memory stand-ins for Azure so the whole suite runs offline (no credentials needed)."""
import datetime as dt
import decimal

import mongomock
import pymongo.errors


class FakeMongoClient(mongomock.MongoClient):
    """mongomock client that mimics Cosmos' CreateDatabase command being rejected (db exists)."""

    def __init__(self, **kwargs):
        super().__init__()

    def __getitem__(self, name):
        db = super().__getitem__(name)

        def _command(*args, **kwargs):
            raise pymongo.errors.OperationFailure("database already exists")

        db.command = _command
        return db


class FakeSqlStore:
    """Dict-backed replacement for SqlStore with the same UNIQUE-per-date behaviour."""

    def __init__(self):
        self.rows = {}

    def get_summary(self, date):
        return self.rows.get(date)

    def save_summary(self, s):
        if s["summary_date"] in self.rows:
            return False  # what the UNIQUE constraint does in Azure SQL
        self.rows[s["summary_date"]] = {
            "summary_date": dt.date.fromisoformat(s["summary_date"]),
            "total_surveys": s["total_surveys"],
            "total_survey_cost": decimal.Decimal(str(s["total_cost"])),
            "top_species": s["top_species"],
            "top_species_sightings": s["top_species_sightings"],
            "low_equipment_alerts": "; ".join(s["low_alerts"]) or None,
            "generated_at": dt.datetime.now(),
        }
        return True

    def list_summaries(self, limit=30):
        return sorted(self.rows.values(), key=lambda r: r["summary_date"], reverse=True)[:limit]
