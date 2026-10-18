# ReefWatch - Marine Conservation PoC (Azure Cosmos DB for MongoDB + Azure SQL + Python)

One monitoring station's proof-of-concept: surveys, equipment and partner organisations live in
**Azure Cosmos DB (MongoDB API)**; end-of-day summaries are computed with **aggregation pipelines**
and stored in **Azure SQL**. Interfaces: CLI menu **and** Flask web app, with PDF reports, charts and
role-based access control.

## Project layout
| File | Purpose |
|------|---------|
| `reefwatch_app.py` | Core: Cosmos + SQL data layers, business logic, seeding, CLI menu |
| `flask_app.py`, `templates/` | Flask web interface (RBAC, CSRF protection, cron hook) |
| `pdf_generator.py` | Survey and daily-summary PDFs (ReportLab) |
| `charts.py` | Survey-trend charts (matplotlib PNGs; the web app uses Chart.js) |
| `auth.py` | Roles + permission table (`admin`, `field_staff`) |
| `config.py` | Reads all settings from `.env` (no secrets in code) |
| `cosmos_setup.py`, `cosmos_schema.json` | Cosmos DB setup script + schema/sample documents |
| `sql_schema.sql` | Azure SQL schema (PK, UNIQUE date, CHECKs, index) |
| `tests/` | 40 automated tests (offline - they use in-memory fakes) |
| `.env.example`, `requirements.txt` | Configuration template and dependencies |

## Prerequisites
Python 3.10+ (tested on 3.12). No ODBC driver is needed: Azure SQL is reached with `pymssql`.

## Setup (fresh environment)
```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # then edit .env (Windows: copy .env.example .env)
```

### Azure configuration
1. **Cosmos DB for MongoDB** account (Free Tier): copy host, port 10255, username and primary password into `.env`.
   *Networking*: allow all networks (or your IP) so markers can connect.
2. **Azure SQL Database** (Free offer): create a server + database, set `SQL_*` in `.env`.
   *Firewall*: add a rule `0.0.0.0` - `255.255.255.255` and enable "Allow Azure services".
3. Create the structures (both are idempotent):
   ```bash
   python cosmos_setup.py                 # database, 3 collections, indexes
   python reefwatch_app.py setup          # Cosmos setup + runs sql_schema.sql
   ```
   To run the SQL script by hand instead, paste `sql_schema.sql` into the Azure portal *Query editor*.
4. Load the test data (12 equipment items, 4 partners, 4 researchers, ~26 surveys, 6 daily summaries):
   ```bash
   python reefwatch_app.py seed
   ```

## Running
```bash
python reefwatch_app.py                 # login + interactive menu
python reefwatch_app.py web             # web UI at http://localhost:5000
python reefwatch_app.py summary         # create today's summary (schedule at 23:55 with cron / Task Scheduler)
python reefwatch_app.py summary 2026-10-12
```
Logins (change in `.env`): `admin` / `admin123` - everything; `staff` / `staff123` - add surveys, view equipment, alerts, survey PDFs.
PDFs and PNG charts are written to `reports/`.

## Tests
```bash
pip install pytest mongomock
python -m pytest
```

## Deploying to Azure App Service (live demo)
1. Create a **Web App** (Linux, Python 3.11/3.12, Free F1 or B1 plan) and deploy this folder
   (zip deploy, GitHub Actions or `az webapp up`).
2. *Settings -> Environment variables*: add every key from `.env` (MONGO_*, SQL_*, FLASK_SECRET_KEY, ADMIN_PASSWORD, STAFF_PASSWORD, optionally CRON_TOKEN).
3. *Settings -> Configuration -> Startup command*:
   `gunicorn --bind=0.0.0.0 --timeout 600 flask_app:app`
4. Scheduled end-of-day summary (optional): a WebJob / Logic App / cron posting
   `POST https://<app>.azurewebsites.net/cron/daily-summary` with header `X-Cron-Token: <CRON_TOKEN>`.
   Duplicates are impossible (UNIQUE constraint).

## Troubleshooting
* *Mongo timeout*: check Cosmos *Networking* allows your IP, and that port 10255 is not blocked by your network.
* *SQL login failed / cannot open server*: check the firewall rule and `SQL_*` values.
* *Free tier throughput*: `cosmos_setup.py` requests shared 400 RU/s so 3 collections stay inside the free 1000 RU/s.
