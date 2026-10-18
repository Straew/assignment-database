"""
Azure Cosmos DB for MongoDB - database / collection / index setup.

Run directly to create everything (idempotent)::

    python cosmos_setup.py                 # create DB + collections + indexes
    python cosmos_setup.py --write-schema  # also write cosmos_schema.json (sample documents)

Design notes
------------
* One shared-throughput database (400 RU/s) keeps all three collections inside the free tier.
* Related data is **embedded** (researcher, species and an equipment snapshot live
  inside each survey; contact details live inside each partner) so every screen is
  served by a single read - the main reason to choose a document store here.
* Cosmos DB for MongoDB needs an index on every field that is filtered or sorted
  on, so each query pattern below has a matching index.
* Collections are left unsharded for this one-station proof of concept. For the
  30+ station roll-out, ``surveys`` would be sharded (Cosmos' "partition key") on
  a ``station_id`` / ``survey_date`` field.
"""
import argparse
import json
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError

import config as cfg

#: collection name -> fields to index (and the query each one serves)
COLLECTION_INDEXES = {
    cfg.COLLECTIONS["surveys"]: ["survey_date",   # daily totals, species counts, trends ($match)
                                 "created_at"],   # latest-first survey listing
    cfg.COLLECTIONS["equipment"]: ["name",        # alphabetical listing
                                   "category"],   # grouping / filtering by category
    cfg.COLLECTIONS["partners"]: ["org_name"],    # alphabetical listing
}

#: sample documents that document the schema of each collection
SAMPLE_DOCUMENTS = {
    "surveys": {
        "_id": "sv-1a2b3c4d",
        "survey_date": "2026-10-12",
        "site": "Heron Island North",
        "researcher": {"name": "Dr Maya Chen", "email": "maya.chen@reefwatch.example",
                       "institution": "ReefWatch"},
        "species_observed": [{"name": "Clownfish", "count": 12}, {"name": "Green Turtle", "count": 2}],
        "equipment_used": [{"equipment_id": "eq-001", "name": "Dive tank 12L",
                            "category": "Diving", "quantity": 2}],
        "survey_cost": 840.50,
        "notes": "Good visibility, light current.",
        "created_at": "2026-10-12T10:15:00+00:00",
    },
    "equipment": {
        "_id": "eq-001", "name": "Dive tank 12L", "category": "Diving",
        "replacement_cost": 450.0, "available_quantity": 18, "low_stock_threshold": 5,
    },
    "partner_organisations": {
        "_id": "org-001", "org_name": "Great Barrier Reef Foundation", "org_type": "Foundation",
        "contact": {"name": "Ava Thompson", "email": "ava@gbrf.example", "phone": "07 5550 0101"},
        "conservation_areas": ["Northern GBR", "Coral restoration"],
    },
}


def get_client():
    """Create a MongoClient configured for Cosmos DB for MongoDB (TLS, no retryable writes)."""
    return MongoClient(
        host=cfg.MONGO_HOST, port=cfg.MONGO_PORT, username=cfg.MONGO_USER,
        password=cfg.MONGO_PASSWORD, tls=True, replicaSet="globaldb", retryWrites=False,
        maxIdleTimeMS=120000, appName=f"@{cfg.MONGO_USER}@", serverSelectionTimeoutMS=30000)


def ensure_database(client, db_name=None):
    """
    Create (if missing) the shared-throughput database, the three collections and
    their indexes. Safe to call on every start-up. Returns the database handle.
    """
    db = client[db_name or cfg.MONGO_DATABASE]
    try:  # Cosmos extension command; fails harmlessly if the database already exists
        db.command({"customAction": "CreateDatabase", "offerThroughput": cfg.MONGO_THROUGHPUT})
    except PyMongoError:
        pass
    for collection, fields in COLLECTION_INDEXES.items():
        for field in fields:
            db[collection].create_index(field)  # also creates the collection; idempotent
    return db


def write_schema_file(path="cosmos_schema.json"):
    """Write the collection definitions + sample documents to a JSON file."""
    schema = {
        "database": cfg.MONGO_DATABASE,
        "throughput": f"shared {cfg.MONGO_THROUGHPUT} RU/s",
        "collections": {name: {"indexes": fields} for name, fields in COLLECTION_INDEXES.items()},
        "sample_documents": SAMPLE_DOCUMENTS,
    }
    Path(path).write_text(json.dumps(schema, indent=2), encoding="utf-8")
    return path


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Set up the ReefWatch Cosmos DB (MongoDB API) database")
    parser.add_argument("--write-schema", action="store_true", help="write cosmos_schema.json")
    args = parser.parse_args()
    if args.write_schema:
        print("Wrote", write_schema_file())
        return
    db = ensure_database(get_client())
    print(f"Database '{db.name}' ready. Collections: {', '.join(sorted(db.list_collection_names()))}")


if __name__ == "__main__":
    main()
