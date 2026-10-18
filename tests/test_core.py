"""Core business logic: validation, equipment stock, aggregation, summaries, seeding."""
import datetime as dt
from collections import Counter

import pytest

import reefwatch_app as rw

RESEARCHER = {"name": "Tester", "email": "t@example.com", "institution": "Lab"}
SPECIES = [{"name": "Clownfish", "count": 3}]


# ---- helpers -------------------------------------------------------------
def test_parse_pairs_merges_and_defaults():
    assert rw.parse_pairs("a:2, b, a:1") == [("a", 3), ("b", 1)]
    assert rw.parse_pairs("") == []


@pytest.mark.parametrize("bad", ["a:x", "a:0", ":3", "a:-1"])
def test_parse_pairs_rejects_bad_input(bad):
    with pytest.raises(rw.ReefWatchError):
        rw.parse_pairs(bad)


def test_valid_date():
    assert rw.valid_date(" 2026-10-12 ") == "2026-10-12"
    with pytest.raises(rw.ReefWatchError):
        rw.valid_date("12/10/2026")


def test_document_builders_validate():
    with pytest.raises(rw.ReefWatchError):
        rw.equipment_doc("Tank", "Diving", -5, 1)
    with pytest.raises(rw.ReefWatchError):
        rw.partner_doc("  ", "NGO", {}, [])
    assert rw.equipment_doc("Tank", "diving", 10, 2)["category"] == "Diving"


# ---- equipment tracking --------------------------------------------------------
def test_survey_decrements_equipment(svc):
    eq = svc.cosmos.add_equipment("Tank", "Diving", 450, 10, 2)
    svc.cosmos.add_survey(RESEARCHER, "Site", SPECIES, [(eq["_id"], 3)], 100, "2026-10-01")
    assert svc.cosmos.get_equipment(eq["_id"])["available_quantity"] == 7


def test_survey_rejects_insufficient_or_unknown_equipment(svc):
    eq = svc.cosmos.add_equipment("Tank", "Diving", 450, 2, 1)
    with pytest.raises(rw.ReefWatchError):
        svc.cosmos.add_survey(RESEARCHER, "Site", SPECIES, [(eq["_id"], 5)], 10, "2026-10-01")
    with pytest.raises(rw.ReefWatchError):
        svc.cosmos.add_survey(RESEARCHER, "Site", SPECIES, [("nope", 1)], 10, "2026-10-01")
    assert svc.cosmos.c["surveys"].count_documents({}) == 0
    assert svc.cosmos.get_equipment(eq["_id"])["available_quantity"] == 2


def test_stock_guard_never_goes_negative(svc):
    eq = svc.cosmos.add_equipment("Tank", "Diving", 450, 1, 1)
    with pytest.raises(rw.ReefWatchError):
        svc.cosmos.adjust_quantity(eq["_id"], -2)
    assert svc.cosmos.get_equipment(eq["_id"])["available_quantity"] == 1


def test_failed_survey_write_rolls_back_stock(svc, monkeypatch):
    eq = svc.cosmos.add_equipment("Tank", "Diving", 450, 5, 1)
    monkeypatch.setattr(svc.cosmos.c["surveys"], "insert_one",
                        lambda doc: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        svc.cosmos.add_survey(RESEARCHER, "Site", SPECIES, [(eq["_id"], 2)], 10, "2026-10-01")
    assert svc.cosmos.get_equipment(eq["_id"])["available_quantity"] == 5


def test_restock(svc):
    eq = svc.cosmos.add_equipment("Tank", "Diving", 450, 1, 1)
    assert svc.cosmos.restock(eq["_id"], 4)["available_quantity"] == 5
    with pytest.raises(rw.ReefWatchError):
        svc.cosmos.restock(eq["_id"], 0)


def test_low_equipment_alerts(svc):
    svc.cosmos.add_equipment("Low", "Sensors", 10, 1, 2)
    svc.cosmos.add_equipment("Fine", "Sensors", 10, 9, 2)
    assert [e["name"] for e in svc.cosmos.low_equipment()] == ["Low"]


# ---- survey validation --------------------------------------------------------------
@pytest.mark.parametrize("kwargs", [
    {"cost": -1}, {"species": []}, {"researcher": {"name": " "}}, {"survey_date": "bad"}])
def test_survey_validation(svc, kwargs):
    args = dict(researcher=RESEARCHER, site="S", species=SPECIES, equipment_requests=[],
                cost=1, survey_date="2026-10-01")
    args.update(kwargs)
    with pytest.raises(rw.ReefWatchError):
        svc.cosmos.add_survey(**args)


# ---- aggregation matches the raw data -------------------------------------------------
def test_daily_aggregates_match_raw_documents(seeded):
    date = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    docs = list(seeded.cosmos.c["surveys"].find({"survey_date": date}))
    summary = rw.build_summary(seeded.cosmos, date)
    assert summary["total_surveys"] == len(docs)
    assert summary["total_cost"] == pytest.approx(sum(d["survey_cost"] for d in docs), abs=0.01)
    seen = Counter(s["name"] for d in docs for s in d["species_observed"])
    assert seen[summary["top_species"]] == max(seen.values())


def test_empty_day_summary(svc):
    svc.cosmos.add_equipment("Tank", "Diving", 10, 5, 1)  # (mongomock mis-evaluates $expr on an empty collection)
    s = rw.build_summary(svc.cosmos, "2020-01-01")
    assert (s["total_surveys"], s["total_cost"], s["top_species"]) == (0, 0, None)


def test_trend_has_one_point_per_day(seeded):
    trend = seeded.cosmos.trend(14)
    assert len(trend) == 14 and trend[-1]["date"] == rw.today_str()
    assert sum(p["total_surveys"] for p in trend) == seeded.cosmos.c["surveys"].count_documents({})


# ---- daily summary storage -------------------------------------------------------------
def test_duplicate_summary_is_prevented(svc):
    svc.cosmos.add_equipment("Tank", "Diving", 10, 5, 1)  # (mongomock quirk, see above)
    _, first = rw.generate_daily_summary(svc, "2026-10-01")
    _, second = rw.generate_daily_summary(svc, "2026-10-01")
    assert first is True and second is False and len(svc.sql.rows) == 1


# ---- seeding -----------------------------------------------------------------------------------
def test_seed_meets_testing_requirements_and_is_idempotent(svc):
    rw.seed(svc)
    surveys = svc.cosmos.c["surveys"].count_documents({})
    assert len(svc.cosmos.list_equipment()) >= 10
    assert len(svc.cosmos.list_partners()) >= 3
    assert len({s["researcher"]["name"] for s in svc.cosmos.c["surveys"].find()}) >= 3
    assert len(svc.sql.rows) >= 3
    rw.seed(svc)
    assert svc.cosmos.c["surveys"].count_documents({}) == surveys
    assert len(svc.sql.rows) == 6
