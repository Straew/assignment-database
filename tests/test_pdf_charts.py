"""PDF generation and chart output."""
import pdf_generator
import charts
import reefwatch_app as rw


def test_survey_and_summary_pdfs(seeded, tmp_path):
    survey = seeded.cosmos.list_surveys(1)[0]
    assert pdf_generator.survey_pdf(survey)[:4] == b"%PDF"
    row, _ = rw.generate_daily_summary(seeded, rw.today_str())
    data = pdf_generator.summary_pdf(row)
    path = pdf_generator.write_report(data, "s.pdf", out_dir=str(tmp_path))
    assert open(path, "rb").read(4) == b"%PDF"


def test_charts_written(seeded, tmp_path):
    paths = charts.save_charts(seeded.cosmos.trend(14), seeded.cosmos.species_totals(14), str(tmp_path))
    assert len(paths) == 2 and all(p.endswith(".png") and __import__("os").path.getsize(p) > 1000 for p in paths)


def test_charts_handle_empty_data(tmp_path):
    assert len(charts.save_charts([], [], str(tmp_path))) == 2
