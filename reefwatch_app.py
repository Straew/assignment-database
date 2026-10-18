#!/usr/bin/env python3
"""
ReefWatch Marine Conservation - Proof-of-Concept (one monitoring station)
=========================================================================

Core application: data-access layers for BOTH databases, the business logic,
sample-data seeding and an interactive command-line menu.

* Azure Cosmos DB for MongoDB (NoSQL) -> operational data
      surveys, equipment, partner_organisations (see ``cosmos_setup.py``)
* Azure SQL Database                  -> reporting data
      dbo.DailySummary, one row per date (see ``sql_schema.sql``)

Daily summaries are computed with *MongoDB aggregation pipelines* ($match /
$unwind / $group executed inside the database, not Python loops) and then
stored in Azure SQL. A UNIQUE constraint plus an application check prevent
duplicate summaries for the same date.

Project layout
--------------
reefwatch_app.py   this file (core + CLI)        flask_app.py   web interface
cosmos_setup.py    Cosmos DB setup               pdf_generator.py  PDF reports
sql_schema.sql     Azure SQL schema              charts.py      matplotlib charts
auth.py            roles & permissions           config.py      settings from .env

Usage
-----
    python reefwatch_app.py                 # login + interactive menu
    python reefwatch_app.py setup           # create Cosmos collections/indexes + SQL table
    python reefwatch_app.py seed            # load sample data and summaries
    python reefwatch_app.py summary [DATE]  # end-of-day job (cron / Task Scheduler)
    python reefwatch_app.py web             # run the Flask web interface locally
    python reefwatch_app.py schema          # print the SQL schema script

Assumptions
-----------
* Equipment ``available_quantity`` is decremented when a survey uses it, and
  can be topped up again with the restock option.
* "Most frequently observed species" = the species recorded in the most
  surveys that day (ties broken by total individuals counted).
* The grouped species result is tiny (one row per species), so picking its
  maximum in Python is cheap; all counting/summing happens in the database.
"""

import argparse
import datetime as dt
import getpass
import random
import sys
import uuid
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path

import pymssql
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure, PyMongoError

import auth
import charts
import config as cfg
import cosmos_setup
import pdf_generator

SCHEMA_FILE = Path(__file__).with_name("sql_schema.sql")


class ReefWatchError(ValueError):
    """Validation / business-rule error that is safe to show to the user."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def today_str():
    """Today's date as ``YYYY-MM-DD``."""
    return dt.date.today().isoformat()


def now_iso():
    """Current UTC time as an ISO-8601 string."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def valid_date(text):
    """Validate and normalise a ``YYYY-MM-DD`` string."""
    try:
        return dt.date.fromisoformat(str(text).strip()).isoformat()
    except ValueError:
        raise ReefWatchError(f"'{text}' is not a valid date (use YYYY-MM-DD).")


def parse_pairs(text):
    """
    Parse ``"a:3, b, c:2"`` into ``[("a", 3), ("b", 1), ("c", 2)]``.
    Repeated keys are merged. Used for both species and equipment input.
    """
    totals = {}
    for part in (p.strip() for p in (text or "").split(",")):
        if not part:
            continue
        key, _, num = part.partition(":")
        key, num = key.strip(), num.strip()
        if not num:
            n = 1
        elif num.isdigit():
            n = int(num)
        else:
            raise ReefWatchError(f"'{num}' is not a whole number (in '{part}').")
        if not key or n <= 0:
            raise ReefWatchError(f"Invalid entry '{part}'. Use name:count with count > 0.")
        totals[key] = totals.get(key, 0) + n
    return list(totals.items())


def short_id(prefix):
    """Readable unique id such as ``eq-1a2b3c4d``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------
# Document builders (one place defines each document's shape)
# --------------------------------------------------------------------------
def equipment_doc(name, category, replacement_cost, available_quantity,
                  low_stock_threshold=2, doc_id=None):
    """Build a validated equipment document."""
    if min(replacement_cost, available_quantity, low_stock_threshold) < 0:
        raise ReefWatchError("Cost, quantity and threshold cannot be negative.")
    if not name.strip() or not category.strip():
        raise ReefWatchError("Equipment name and category are required.")
    return {
        "_id": doc_id or short_id("eq"),
        "name": name.strip(),
        "category": category.strip().title(),
        "replacement_cost": round(float(replacement_cost), 2),
        "available_quantity": int(available_quantity),
        "low_stock_threshold": int(low_stock_threshold),
    }


def partner_doc(org_name, org_type, contact, conservation_areas, doc_id=None):
    """Build a partner organisation document. ``contact`` = dict(name, email, phone)."""
    if not org_name.strip():
        raise ReefWatchError("Organisation name is required.")
    return {
        "_id": doc_id or short_id("org"),
        "org_name": org_name.strip(),
        "org_type": (org_type or "Other").strip().title(),
        "contact": contact,
        "conservation_areas": [a.strip() for a in conservation_areas if a.strip()],
    }


def make_survey_doc(researcher, site, species, equipment_used, cost,
                    survey_date, notes="", doc_id=None, created_at=None):
    """
    Build a survey document. Researcher, species and an equipment snapshot are
    *embedded*, so one read returns everything needed for a survey report.
    """
    return {
        "_id": doc_id or short_id("sv"),
        "survey_date": survey_date,          # indexed; every daily query filters on it
        "site": site.strip(),
        "researcher": researcher,            # {name, email, institution}
        "species_observed": species,         # [{name, count}]
        "equipment_used": equipment_used,    # [{equipment_id, name, category, quantity}]
        "survey_cost": round(float(cost), 2),
        "notes": notes.strip(),
        "created_at": created_at or now_iso(),
    }


# --------------------------------------------------------------------------
# Azure Cosmos DB for MongoDB access layer
# --------------------------------------------------------------------------
class CosmosStore:
    """All MongoDB-API reads/writes. ``client`` is injectable for testing."""

    def __init__(self, client=None):
        self.db = cosmos_setup.ensure_database(client or cosmos_setup.get_client())
        self.c = {key: self.db[name] for key, name in cfg.COLLECTIONS.items()}

    # -- generic helpers ---------------------------------------------------
    def save(self, container, doc):
        """Insert or replace a document (keyed on ``_id``) and return it."""
        self.c[container].replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return doc

    def _agg(self, container, pipeline):
        """Run an aggregation pipeline and return its rows as a list."""
        return list(self.c[container].aggregate(pipeline))

    # -- equipment ---------------------------------------------------------
    def add_equipment(self, *args, **kwargs):
        """Create an equipment item (see :func:`equipment_doc` for arguments)."""
        return self.save("equipment", equipment_doc(*args, **kwargs))

    def list_equipment(self):
        """All equipment ordered by name."""
        return list(self.c["equipment"].find().sort("name", 1))

    def get_equipment(self, equipment_id):
        """Find one equipment item by id (or ``None``)."""
        return self.c["equipment"].find_one({"_id": equipment_id})

    def low_equipment(self):
        """Items at or below their own low-stock threshold (compared inside the database)."""
        fields = {"name": 1, "category": 1, "available_quantity": 1, "low_stock_threshold": 1}
        try:
            return list(self.c["equipment"].find(
                {"$expr": {"$lte": ["$available_quantity", "$low_stock_threshold"]}},
                fields).sort("name", 1))
        except OperationFailure:  # server without $expr support: tiny fallback
            return [e for e in self.c["equipment"].find({}, fields).sort("name", 1)
                    if e["available_quantity"] <= e["low_stock_threshold"]]

    def adjust_quantity(self, equipment_id, delta):
        """
        Atomically add ``delta`` to available_quantity ($inc). For a decrease the
        filter ``available_quantity >= amount`` makes stock impossible to go
        negative, even with concurrent users.
        """
        query = {"_id": equipment_id}
        if delta < 0:
            query["available_quantity"] = {"$gte": -delta}
        doc = self.c["equipment"].find_one_and_update(
            query, {"$inc": {"available_quantity": delta}}, return_document=ReturnDocument.AFTER)
        if doc is None:
            raise ReefWatchError("Not enough stock available (or unknown equipment).")
        return doc

    def restock(self, equipment_id, quantity):
        """Add ``quantity`` (> 0) units to an item's available stock."""
        if quantity <= 0:
            raise ReefWatchError("Restock quantity must be greater than zero.")
        return self.adjust_quantity(equipment_id, quantity)

    # -- partners ----------------------------------------------------------
    def add_partner(self, *args, **kwargs):
        """Create a partner organisation (see :func:`partner_doc`)."""
        return self.save("partners", partner_doc(*args, **kwargs))

    def list_partners(self):
        """All partner organisations ordered by name."""
        return list(self.c["partners"].find().sort("org_name", 1))

    # -- surveys -----------------------------------------------------------
    def add_survey(self, researcher, site, species, equipment_requests, cost,
                   survey_date=None, notes=""):
        """
        Validate and store a survey, then decrement the equipment it used.

        :param species: ``[{"name": str, "count": int}]``
        :param equipment_requests: ``[(equipment_id, quantity)]``
        Equipment is decremented first (atomic + guarded); if the survey write
        then fails, the stock changes are rolled back.
        """
        survey_date = valid_date(survey_date or today_str())
        if float(cost) < 0:
            raise ReefWatchError("Survey cost cannot be negative.")
        if not species:
            raise ReefWatchError("Record at least one observed species.")
        if not researcher.get("name", "").strip():
            raise ReefWatchError("Researcher name is required.")

        entries = []
        for eq_id, qty in equipment_requests:
            item = self.get_equipment(eq_id)
            if item is None:
                raise ReefWatchError(f"Unknown equipment id '{eq_id}'.")
            if qty > item["available_quantity"]:
                raise ReefWatchError(
                    f"Only {item['available_quantity']} x {item['name']} available (requested {qty}).")
            entries.append({"equipment_id": item["_id"], "name": item["name"],
                            "category": item["category"], "quantity": qty})

        taken = []
        try:
            for e in entries:
                self.adjust_quantity(e["equipment_id"], -e["quantity"])
                taken.append(e)
            doc = make_survey_doc(researcher, site, species, entries, cost, survey_date, notes)
            self.c["surveys"].insert_one(doc)
            return doc
        except Exception:
            for e in taken:  # roll back stock changes
                self.adjust_quantity(e["equipment_id"], e["quantity"])
            raise

    def list_surveys(self, limit=50):
        """Most recent surveys first."""
        return list(self.c["surveys"].find().sort("created_at", -1).limit(limit))

    def get_survey(self, survey_id):
        """Return one survey document by id (or ``None``)."""
        return self.c["surveys"].find_one({"_id": survey_id})

    # -- aggregation pipelines (executed inside Cosmos DB) -------------------
    def day_totals(self, date):
        """Survey count and total cost for one date ($match + $group)."""
        rows = self._agg("surveys", [
            {"$match": {"survey_date": date}},
            {"$group": {"_id": None, "total_surveys": {"$sum": 1},
                        "total_cost": {"$sum": "$survey_cost"}}}])
        row = rows[0] if rows else {}
        return {"total_surveys": row.get("total_surveys", 0),
                "total_cost": round(row.get("total_cost", 0) or 0, 2)}

    def _species_agg(self, match):
        """Sightings per species for surveys matching ``match`` ($unwind the embedded array)."""
        rows = self._agg("surveys", [
            {"$match": match},
            {"$unwind": "$species_observed"},
            {"$group": {"_id": "$species_observed.name", "sightings": {"$sum": 1},
                        "individuals": {"$sum": "$species_observed.count"}}}])
        return [{"species": r["_id"], "sightings": r["sightings"], "individuals": r["individuals"]}
                for r in rows]

    def species_counts(self, date):
        """Sightings per species for one date."""
        return self._species_agg({"survey_date": date})

    def trend(self, days=14):
        """Per-day surveys/cost for the last ``days`` days (one $group; empty days filled with 0)."""
        end = dt.date.today()
        dates = [(end - dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        rows = self._agg("surveys", [
            {"$match": {"survey_date": {"$gte": dates[0]}}},
            {"$group": {"_id": "$survey_date", "total_surveys": {"$sum": 1},
                        "total_cost": {"$sum": "$survey_cost"}}}])
        by_date = {r["_id"]: r for r in rows}
        return [{"date": d, "total_surveys": by_date.get(d, {}).get("total_surveys", 0),
                 "total_cost": round(by_date.get(d, {}).get("total_cost", 0), 2)} for d in dates]

    def species_totals(self, days=14, top=8):
        """Top species over the last ``days`` days as ``[(name, surveys_seen_in)]``."""
        start = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
        rows = self._species_agg({"survey_date": {"$gte": start}})
        rows.sort(key=lambda r: r["sightings"], reverse=True)
        return [(r["species"], r["sightings"]) for r in rows[:top]]


# --------------------------------------------------------------------------
# Azure SQL access layer
# --------------------------------------------------------------------------
class SqlStore:
    """Reads/writes the ``dbo.DailySummary`` table in Azure SQL (via pymssql)."""

    COLUMNS = ("summary_date, total_surveys, total_survey_cost, top_species, "
               "top_species_sightings, low_equipment_alerts, generated_at")

    def __init__(self):
        self.init_schema()

    @contextmanager
    def _cursor(self, commit=False):
        """Open a connection/cursor (dict rows), commit if asked, always close."""
        conn = pymssql.connect(server=cfg.SQL_SERVER, user=cfg.SQL_USERNAME,
                               password=cfg.SQL_PASSWORD, database=cfg.SQL_DATABASE,
                               port=1433, login_timeout=30, as_dict=True)
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()

    def init_schema(self):
        """Run ``sql_schema.sql`` (idempotent: objects are only created if missing)."""
        with self._cursor(commit=True) as cur:
            cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

    def get_summary(self, date):
        """Stored summary for ``date`` or ``None``."""
        with self._cursor() as cur:
            cur.execute(f"SELECT {self.COLUMNS} FROM dbo.DailySummary WHERE summary_date = %s", (date,))
            return cur.fetchone()

    def list_summaries(self, limit=30):
        """Most recent summaries first."""
        with self._cursor() as cur:
            cur.execute(f"SELECT TOP (%s) {self.COLUMNS} FROM dbo.DailySummary "
                        "ORDER BY summary_date DESC", (limit,))
            return cur.fetchall()

    def save_summary(self, s):
        """
        Insert a summary. Returns ``False`` (nothing written) if one already exists
        for that date - enforced by the UNIQUE constraint, so it is safe even if two
        processes race.
        """
        try:
            with self._cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO dbo.DailySummary (summary_date, total_surveys, total_survey_cost, "
                    "top_species, top_species_sightings, low_equipment_alerts) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (s["summary_date"], s["total_surveys"], s["total_cost"], s["top_species"],
                     s["top_species_sightings"], "; ".join(s["low_alerts"]) or None))
            return True
        except pymssql.IntegrityError:
            return False


# --------------------------------------------------------------------------
# Services container + business logic
# --------------------------------------------------------------------------
class Services:
    """Lazily creates the two database connections (importing never connects)."""

    @cached_property
    def cosmos(self):
        return CosmosStore()

    @cached_property
    def sql(self):
        return SqlStore()


def build_summary(cosmos, date):
    """Compute a day's summary using Cosmos DB aggregation pipelines."""
    totals = cosmos.day_totals(date)
    top = max(cosmos.species_counts(date),
              key=lambda r: (r["sightings"], r["individuals"]), default=None)
    return {
        "summary_date": date,
        "total_surveys": totals["total_surveys"],
        "total_cost": totals["total_cost"],
        "top_species": top["species"] if top else None,
        "top_species_sightings": top["sightings"] if top else None,
        "low_alerts": [f"{e['name']} ({e['available_quantity']} left, min {e['low_stock_threshold']})"
                       for e in cosmos.low_equipment()],
    }


def generate_daily_summary(svc, date):
    """
    Generate and store the summary for ``date`` unless one already exists.

    :returns: ``(summary_row, created)``; ``created`` is False for a duplicate.
    """
    date = valid_date(date)
    existing = svc.sql.get_summary(date)
    if existing:
        return existing, False
    if not svc.sql.save_summary(build_summary(svc.cosmos, date)):
        return svc.sql.get_summary(date), False  # lost a race with another process
    return svc.sql.get_summary(date), True


def format_summary(s):
    """Human-readable multi-line summary for the CLI."""
    top = f"{s['top_species']} ({s['top_species_sightings']} surveys)" if s["top_species"] else "n/a"
    return (f"  Date            : {s['summary_date']}\n"
            f"  Surveys         : {s['total_surveys']}\n"
            f"  Total cost      : ${float(s['total_survey_cost']):,.2f}\n"
            f"  Top species     : {top}\n"
            f"  Equipment alerts: {s['low_equipment_alerts'] or 'none'}")


# --------------------------------------------------------------------------
# Sample data for testing / demo
# --------------------------------------------------------------------------
SEED_EQUIPMENT = [  # (name, category, replacement cost, qty, low threshold)
    ("Dive tank 12L", "Diving", 450, 20, 5), ("Regulator set", "Diving", 900, 10, 3),
    ("Buoyancy compensator", "Diving", 700, 8, 2), ("Underwater camera", "Imaging", 3200, 4, 1),
    ("Action camera", "Imaging", 650, 6, 2), ("Survey drone", "Imaging", 2100, 3, 1),
    ("Transect tape 50m", "Survey", 85, 15, 4), ("Quadrat 1m x 1m", "Survey", 60, 12, 3),
    ("Underwater slate", "Survey", 25, 30, 8), ("Water-quality probe", "Sensors", 2400, 2, 1),
    ("Hydrophone", "Sensors", 1800, 1, 1), ("Temperature logger", "Sensors", 320, 9, 3),
]
SEED_PARTNERS = [
    ("Great Barrier Reef Foundation", "Foundation", ("Ava Thompson", "ava@gbrf.example", "07 5550 0101"),
     ["Northern GBR", "Coral restoration"]),
    ("James Cook University Marine Lab", "University", ("Dr Ben Walker", "ben.walker@jcu.example", "07 5550 0102"),
     ["Heron Island", "Larval ecology"]),
    ("Sunshine Coast Dive Alliance", "Community", ("Priya Nair", "priya@scda.example", "07 5550 0103"),
     ["Sunshine Coast", "Citizen science"]),
    ("Pacific Turtle Trust", "NGO", ("Tane Rangi", "tane@ptt.example", "07 5550 0104"),
     ["Coral Sea", "Turtle nesting"]),
]
SEED_RESEARCHERS = [
    {"name": "Dr Maya Chen", "email": "maya.chen@reefwatch.example", "institution": "ReefWatch"},
    {"name": "Liam O'Connor", "email": "liam.oconnor@uq.example", "institution": "University of Queensland"},
    {"name": "Dr Aroha Ngata", "email": "aroha.ngata@jcu.example", "institution": "James Cook University"},
    {"name": "Sofia Rossi", "email": "sofia.rossi@reefwatch.example", "institution": "ReefWatch"},
]
SEED_SITES = ["Heron Island North", "Lady Elliot Reef", "Osprey Reef", "Cod Hole", "Moore Reef"]
SEED_SPECIES = ["Clownfish", "Green Turtle", "Reef Shark", "Manta Ray", "Parrotfish",
                "Staghorn Coral", "Giant Clam", "Humphead Wrasse", "Moorish Idol", "Sea Cucumber"]


def seed(svc, days=6):
    """Populate both databases with repeatable sample data (idempotent). Returns days seeded."""
    cosmos = svc.cosmos
    eq_docs = [cosmos.save("equipment", equipment_doc(n, c, cost, q, t, doc_id=f"eq-{i:03d}"))
               for i, (n, c, cost, q, t) in enumerate(SEED_EQUIPMENT, 1)]
    for i, (name, typ, (cn, ce, cp), areas) in enumerate(SEED_PARTNERS, 1):
        cosmos.save("partners", partner_doc(name, typ, {"name": cn, "email": ce, "phone": cp},
                                            areas, doc_id=f"org-{i:03d}"))
    rng = random.Random(42)
    dates = [(dt.date.today() - dt.timedelta(days=d)).isoformat() for d in range(days, 0, -1)]
    for date in dates:
        for n in range(1, rng.randint(3, 5) + 1):
            used = [{"equipment_id": e["_id"], "name": e["name"], "category": e["category"],
                     "quantity": rng.randint(1, 2)} for e in rng.sample(eq_docs, 3)]
            species = [{"name": s, "count": rng.randint(1, 25)}
                       for s in rng.sample(SEED_SPECIES, rng.randint(2, 4))]
            cosmos.save("surveys", make_survey_doc(
                rng.choice(SEED_RESEARCHERS), rng.choice(SEED_SITES), species, used,
                rng.uniform(250, 1800), date, "Seeded sample survey.",
                doc_id=f"seed-{date}-{n}", created_at=f"{date}T10:{n:02d}:00+00:00"))
    for date in dates:
        generate_daily_summary(svc, date)
    return len(dates)


# --------------------------------------------------------------------------
# Interactive CLI
# --------------------------------------------------------------------------
def ask(prompt, cast=str, default=None):
    """Prompt until the input can be cast to the wanted type (blank -> default)."""
    while True:
        raw = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
        if not raw and default is not None:
            return default
        try:
            if not raw and cast is str:
                raise ValueError("A value is required.")
            return cast(raw)
        except ValueError:
            print("  ! Invalid input, try again.")


def print_table(rows, columns):
    """Print dict rows as an aligned text table. ``columns`` = [(header, key_or_callable)]."""
    if not rows:
        print("  (nothing to show)")
        return
    get = lambda r, k: str(k(r) if callable(k) else r.get(k, ""))
    widths = [max(len(h), *(len(get(r, k)) for r in rows)) for h, k in columns]
    print("  " + " | ".join(h.ljust(w) for (h, _), w in zip(columns, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in rows:
        print("  " + " | ".join(get(r, k).ljust(w) for (_, k), w in zip(columns, widths)))


def _pick(items, columns, prompt):
    """Show a numbered list and return the chosen item."""
    print_table([{**it, "no": i} for i, it in enumerate(items, 1)], [("#", "no")] + columns)
    index = ask(prompt, int)
    if not 0 < index <= len(items):
        raise ReefWatchError("That number is not in the list.")
    return items[index - 1]


def cli_add_survey(svc):
    equipment = svc.cosmos.list_equipment()
    print_table([{**e, "no": i} for i, e in enumerate(equipment, 1)],
                [("#", "no"), ("Item", "name"), ("Category", "category"), ("Available", "available_quantity")])
    researcher = {"name": ask("Researcher name"), "email": ask("Researcher email", default=""),
                  "institution": ask("Institution", default="")}
    site = ask("Site")
    date = ask("Survey date (YYYY-MM-DD)", valid_date, today_str())
    species = [{"name": n.title(), "count": c} for n, c in ask("Species (name:count, ...)", parse_pairs)]
    picks = ask("Equipment used (#:qty, ...; blank for none)", parse_pairs, "")
    requests = [(equipment[int(k) - 1]["_id"], q) for k, q in (picks or [])
                if k.isdigit() and 0 < int(k) <= len(equipment)]
    saved = svc.cosmos.add_survey(researcher, site, species, requests, ask("Survey cost ($)", float),
                                  date, ask("Notes", default=""))
    print(f"  Saved survey {saved['_id']} and updated equipment stock.")
    print("  PDF report:", pdf_generator.write_report(pdf_generator.survey_pdf(saved), f"survey-{saved['_id']}.pdf"))


def cli_survey_pdf(svc):
    s = _pick(svc.cosmos.list_surveys(20), [("Date", "survey_date"), ("Site", "site"),
              ("Researcher", lambda r: r["researcher"]["name"])], "Survey # to export")
    print("  Saved", pdf_generator.write_report(pdf_generator.survey_pdf(s), f"survey-{s['_id']}.pdf"))


def cli_add_equipment(svc):
    doc = svc.cosmos.add_equipment(ask("Name"), ask("Category"), ask("Replacement cost", float),
                                   ask("Available quantity", int), ask("Low-stock threshold", int, 2))
    print(f"  Added {doc['name']} ({doc['_id']}).")


def cli_restock(svc):
    item = _pick(svc.cosmos.list_equipment(), [("Item", "name"), ("Available", "available_quantity")],
                 "Item # to restock")
    updated = svc.cosmos.restock(item["_id"], ask("Units to add", int))
    print(f"  {updated['name']} now has {updated['available_quantity']} available.")


def cli_add_partner(svc):
    doc = svc.cosmos.add_partner(
        ask("Organisation name"), ask("Type (University/NGO/...)", default="Other"),
        {"name": ask("Contact name", default=""), "email": ask("Contact email", default=""),
         "phone": ask("Contact phone", default="")},
        ask("Conservation areas (comma-separated)", default="").split(","))
    print(f"  Added {doc['org_name']}.")


def cli_summary(svc):
    row, created = generate_daily_summary(svc, ask("Date (YYYY-MM-DD)", valid_date, today_str()))
    print("  Generated and stored in Azure SQL:" if created
          else "  A summary already exists for that date (no duplicate created):")
    print(format_summary(row))
    if created:
        print("  PDF report:", pdf_generator.write_report(
            pdf_generator.summary_pdf(row), f"summary-{row['summary_date']}.pdf"))


def cli_summary_pdf(svc):
    date = ask("Summary date (YYYY-MM-DD)", valid_date, today_str())
    row = svc.sql.get_summary(date)
    if not row:
        raise ReefWatchError("No stored summary for that date - generate it first.")
    print("  Saved", pdf_generator.write_report(pdf_generator.summary_pdf(row), f"summary-{date}.pdf"))


def cli_charts(svc):
    for path in charts.save_charts(svc.cosmos.trend(14), svc.cosmos.species_totals(14)):
        print("  Saved", path)


MENU = {  # key -> (label, handler, permission required)
    "1": ("Add survey", cli_add_survey, "survey.add"),
    "2": ("List equipment", lambda s: print_table(s.cosmos.list_equipment(), [
        ("Item", "name"), ("Category", "category"), ("Cost", "replacement_cost"),
        ("Available", "available_quantity"), ("Min", "low_stock_threshold")]), "equipment.view"),
    "3": ("Low-equipment alerts", lambda s: print_table(s.cosmos.low_equipment(), [
        ("Item", "name"), ("Left", "available_quantity"), ("Min", "low_stock_threshold")]), "alerts.view"),
    "4": ("Export survey report (PDF)", cli_survey_pdf, "survey.pdf"),
    "5": ("Add equipment", cli_add_equipment, "equipment.manage"),
    "6": ("Restock equipment", cli_restock, "equipment.manage"),
    "7": ("Add partner organisation", cli_add_partner, "partner.manage"),
    "8": ("List partner organisations", lambda s: print_table(s.cosmos.list_partners(), [
        ("Organisation", "org_name"), ("Type", "org_type"), ("Contact", lambda r: r["contact"]["name"]),
        ("Areas", lambda r: ", ".join(r["conservation_areas"]))]), "partner.manage"),
    "9": ("Generate daily summary", cli_summary, "summary.generate"),
    "10": ("View stored daily summaries", lambda s: print_table(s.sql.list_summaries(), [
        ("Date", "summary_date"), ("Surveys", "total_surveys"),
        ("Cost", lambda r: f"${float(r['total_survey_cost']):,.2f}"),
        ("Top species", lambda r: r["top_species"] or "-")]), "summary.view"),
    "11": ("Export daily summary (PDF)", cli_summary_pdf, "summary.pdf"),
    "12": ("Generate survey trend charts (PNG)", cli_charts, "charts.view"),
}


def login():
    """Prompt for credentials (3 attempts). Returns the user's role or ``None``."""
    for _ in range(3):
        role = auth.authenticate(input("Username: "), getpass.getpass("Password: "))
        if role:
            return role
        print("  ! Invalid username or password.")
    return None


def run_menu(svc, role):
    """Permission-filtered menu loop; errors are reported, never crash the app."""
    allowed = {k: v for k, v in MENU.items() if auth.can(role, v[2])}
    while True:
        print(f"\n=== ReefWatch ({role}) ===")
        for key, (label, _, _) in allowed.items():
            print(f" {key:>2}. {label}")
        choice = input("  q. Quit\n> ").strip().lower()
        if choice == "q":
            return
        if choice not in allowed:
            print("  ! Unknown option (or your role does not allow it).")
            continue
        try:
            allowed[choice][1](svc)
        except (ValueError, PyMongoError, pymssql.Error, OSError) as exc:
            print(f"  ! {exc}")


def main(argv=None):
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="ReefWatch proof-of-concept")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("menu", help="login + interactive menu (default)")
    sub.add_parser("setup", help="create Cosmos collections/indexes and the SQL table")
    sub.add_parser("seed", help="load sample data and daily summaries")
    sub.add_parser("schema", help="print the SQL schema script")
    p_sum = sub.add_parser("summary", help="generate a daily summary (for scheduled jobs)")
    p_sum.add_argument("date", nargs="?", default=today_str())
    p_web = sub.add_parser("web", help="run the Flask web interface locally")
    p_web.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)

    if args.command == "schema":
        print(SCHEMA_FILE.read_text(encoding="utf-8"))
    elif args.command == "web":
        import flask_app  # imported lazily so the CLI never needs Flask running
        flask_app.app.run(host="0.0.0.0", port=args.port)
    else:
        svc = Services()
        if args.command == "setup":
            svc.cosmos, svc.sql  # touching the properties creates everything
            print("Cosmos DB (MongoDB API) collections and Azure SQL table are ready.")
        elif args.command == "seed":
            print(f"Seeded equipment, partners, surveys and {seed(svc)} daily summaries.")
        elif args.command == "summary":
            row, created = generate_daily_summary(svc, args.date)
            print(("Created:\n" if created else "Already exists:\n") + format_summary(row))
        else:
            role = login()
            if role is None:
                sys.exit("Too many failed login attempts.")
            run_menu(svc, role)
    return 0


if __name__ == "__main__":
    sys.exit(main())
