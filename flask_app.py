"""
ReefWatch Flask web interface (survey entry, equipment, partners, summaries, PDFs, charts).

* Role-based access control via ``auth.py`` (admin vs field_staff), one permission per route
* CSRF protection on every POST, HTTP-only / SameSite session cookies
* Optional ``POST /cron/daily-summary`` (header ``X-Cron-Token``) so Azure can create the
  end-of-day summary on a schedule; duplicates are impossible (UNIQUE constraint)

Run locally   :  python reefwatch_app.py web      (or ``flask --app flask_app run``)
Azure hosting :  gunicorn --bind=0.0.0.0 --timeout 600 flask_app:app
"""
import hmac
import io
import secrets
from functools import wraps

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

import auth
import config as cfg
import pdf_generator
from reefwatch_app import (ReefWatchError, Services, build_summary, generate_daily_summary,
                           parse_pairs, today_str, valid_date)


def create_app(services=None):
    """Application factory. ``services`` can be injected for testing."""
    app = Flask(__name__)
    app.config.update(SECRET_KEY=cfg.FLASK_SECRET_KEY, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax")
    svc = services or Services()  # database connections are opened lazily on first use

    # ---- security helpers ------------------------------------------------
    def permission_required(permission):
        """Route decorator: must be logged in and hold ``permission``."""
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if "user" not in session:
                    return redirect(url_for("login"))
                if not auth.can(session.get("role"), permission):
                    abort(403)
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    @app.before_request
    def csrf_protect():
        """Reject POSTs that lack the session's CSRF token (token-protected cron route exempt)."""
        if request.method == "POST" and request.endpoint != "cron_daily_summary":
            expected = session.get("_csrf", "")
            if not expected or not hmac.compare_digest(expected, request.form.get("_csrf", "")):
                abort(400, "Invalid or missing CSRF token.")

    @app.context_processor
    def template_globals():
        """Expose the CSRF token and a permission checker to every template."""
        session.setdefault("_csrf", secrets.token_hex(16))
        return {"csrf_token": session["_csrf"],
                "can": lambda permission: auth.can(session.get("role"), permission)}

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("forbidden.html"), 403

    # ---- small helpers -----------------------------------------------------
    def guarded(action, back):
        """Run ``action`` (returns a success message); flash errors; redirect to ``back``."""
        try:
            flash(action(), "ok")
        except ValueError as exc:  # includes ReefWatchError
            flash(str(exc), "err")
        return redirect(url_for(back))

    def pdf_response(data, name):
        return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name)

    # ---- authentication ------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            role = auth.authenticate(request.form["username"], request.form["password"])
            if role:
                csrf = session.get("_csrf")
                session.clear()
                session.update(user=request.form["username"].strip().lower(), role=role, _csrf=csrf)
                return redirect(url_for("dashboard"))
            flash("Invalid username or password.", "err")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---- dashboard -------------------------------------------------------------
    @app.route("/")
    @permission_required("dashboard")
    def dashboard():
        return render_template("dashboard.html", s=build_summary(svc.cosmos, today_str()))

    # ---- surveys ---------------------------------------------------------------
    @app.route("/surveys")
    @permission_required("survey.view")
    def surveys():
        return render_template("surveys.html", items=svc.cosmos.list_surveys())

    @app.route("/surveys/new", methods=["GET", "POST"])
    @permission_required("survey.add")
    def survey_new():
        if request.method == "GET":
            return render_template("survey_new.html", equipment=svc.cosmos.list_equipment(),
                                   today=today_str())
        f = request.form

        def action():
            svc.cosmos.add_survey(
                {"name": f["r_name"], "email": f["r_email"], "institution": f["r_inst"]},
                f["site"],
                [{"name": n.title(), "count": c} for n, c in parse_pairs(f["species"])],
                [(k[4:], int(v)) for k, v in f.items()
                 if k.startswith("qty_") and v.strip() and int(v) > 0],
                float(f["cost"]), f["date"], f["notes"])
            return "Survey saved and equipment stock updated."
        return guarded(action, "survey_new")

    @app.route("/surveys/<sid>/pdf")
    @permission_required("survey.pdf")
    def survey_pdf_route(sid):
        survey = svc.cosmos.get_survey(sid) or abort(404)
        return pdf_response(pdf_generator.survey_pdf(survey), f"survey-{sid}.pdf")

    # ---- equipment ---------------------------------------------------------------
    @app.route("/equipment")
    @permission_required("equipment.view")
    def equipment():
        return render_template("equipment.html", items=svc.cosmos.list_equipment())

    @app.route("/equipment/new", methods=["POST"])
    @permission_required("equipment.manage")
    def equipment_new():
        f = request.form
        return guarded(lambda: svc.cosmos.add_equipment(
            f["name"], f["category"], float(f["cost"]), int(f["qty"]), int(f["threshold"]))
            and "Equipment added.", "equipment")

    @app.route("/equipment/<eid>/restock", methods=["POST"])
    @permission_required("equipment.manage")
    def equipment_restock(eid):
        return guarded(lambda: svc.cosmos.restock(eid, int(request.form["qty"]))
                       and "Stock updated.", "equipment")

    # ---- partners ------------------------------------------------------------------
    @app.route("/partners")
    @permission_required("partner.manage")
    def partners():
        return render_template("partners.html", items=svc.cosmos.list_partners())

    @app.route("/partners/new", methods=["POST"])
    @permission_required("partner.manage")
    def partner_new():
        f = request.form
        return guarded(lambda: svc.cosmos.add_partner(
            f["org_name"], f["org_type"],
            {"name": f["c_name"], "email": f["c_email"], "phone": f["c_phone"]},
            f["areas"].split(",")) and "Partner added.", "partners")

    # ---- daily summaries -------------------------------------------------------------
    @app.route("/summaries")
    @permission_required("summary.view")
    def summaries():
        return render_template("summaries.html", items=svc.sql.list_summaries(), today=today_str())

    @app.route("/summaries/generate", methods=["POST"])
    @permission_required("summary.generate")
    def summary_generate():
        def action():
            date = valid_date(request.form["date"])
            _, created = generate_daily_summary(svc, date)
            return (f"Summary for {date} generated." if created
                    else f"A summary for {date} already exists - no duplicate created.")
        return guarded(action, "summaries")

    @app.route("/summaries/<date>/pdf")
    @permission_required("summary.pdf")
    def summary_pdf_route(date):
        row = svc.sql.get_summary(valid_date(date)) or abort(404)
        return pdf_response(pdf_generator.summary_pdf(row), f"summary-{date}.pdf")

    @app.route("/cron/daily-summary", methods=["POST"])
    def cron_daily_summary():
        """Scheduler hook (Azure WebJob / Logic App / cron). Disabled unless CRON_TOKEN is set."""
        token = request.headers.get("X-Cron-Token", "")
        if not cfg.CRON_TOKEN:
            abort(404)
        if not hmac.compare_digest(cfg.CRON_TOKEN, token):
            abort(403)
        date = valid_date(request.args.get("date", today_str()))
        _, created = generate_daily_summary(svc, date)
        return jsonify(date=date, created=created)

    # ---- charts --------------------------------------------------------------------------
    @app.route("/charts")
    @permission_required("charts.view")
    def charts_page():
        return render_template("charts.html", trend=svc.cosmos.trend(14),
                               species=svc.cosmos.species_totals(14))

    return app


app = create_app()  # module-level WSGI object for gunicorn / Azure App Service

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
