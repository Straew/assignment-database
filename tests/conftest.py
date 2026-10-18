"""Shared fixtures: services backed by mongomock + an in-memory SQL fake."""
import os

# Tests must not depend on the developer's real .env (e.g. changed login passwords).
# python-dotenv never overrides variables that already exist, so set them BEFORE config is imported.
os.environ.update(ADMIN_PASSWORD="admin123", STAFF_PASSWORD="staff123", CRON_TOKEN="")

import pytest

from reefwatch_app import CosmosStore
from tests.fakes import FakeMongoClient, FakeSqlStore


class FakeServices:
    """Same attribute names as ``reefwatch_app.Services``."""

    def __init__(self):
        self.cosmos = CosmosStore(client=FakeMongoClient())
        self.sql = FakeSqlStore()


@pytest.fixture()
def svc():
    return FakeServices()


@pytest.fixture()
def seeded(svc):
    import reefwatch_app
    reefwatch_app.seed(svc)
    return svc
