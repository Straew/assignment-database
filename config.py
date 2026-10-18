"""
Central configuration for ReefWatch.

Every setting comes from environment variables, loaded from a ``.env`` file
sitting next to this module (see ``.env.example``). No secrets live in code.
"""
import os
from pathlib import Path

try:  # python-dotenv is in requirements.txt; tolerate its absence in odd setups
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
    load_dotenv()  # also honour a .env in the current working directory
except ImportError:  # pragma: no cover
    pass

# --- Azure Cosmos DB for MongoDB (operational data) -------------------------
MONGO_HOST = os.getenv("MONGO_HOST", "YOUR-ACCOUNT.mongo.cosmos.azure.com")
MONGO_PORT = int(os.getenv("MONGO_PORT", "10255"))
MONGO_USER = os.getenv("MONGO_USER", "YOUR-ACCOUNT")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "YOUR-PRIMARY-PASSWORD")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "ReefWatchDB")
MONGO_THROUGHPUT = int(os.getenv("MONGO_THROUGHPUT", "400"))  # shared RU/s (free tier friendly)

#: logical name -> collection name in Cosmos DB
COLLECTIONS = {
    "surveys": os.getenv("COSMOS_SURVEYS_COLLECTION", "surveys"),
    "equipment": os.getenv("COSMOS_EQUIPMENT_COLLECTION", "equipment"),
    "partners": os.getenv("COSMOS_PARTNERS_COLLECTION", "partner_organisations"),
}

# --- Azure SQL Database (daily summaries) ------------------------------------
SQL_SERVER = os.getenv("SQL_SERVER", "YOUR-SERVER.database.windows.net")
SQL_DATABASE = os.getenv("SQL_DATABASE", "ReefWatchDB")
SQL_USERNAME = os.getenv("SQL_USERNAME", "YOUR-SQL-USER")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "YOUR-SQL-PASSWORD")

# --- Application / web ---------------------------------------------------------
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
STAFF_PASSWORD = os.getenv("STAFF_PASSWORD", "staff123")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
CRON_TOKEN = os.getenv("CRON_TOKEN", "")  # enables POST /cron/daily-summary when set
REPORT_DIR = os.getenv("REPORT_DIR", "reports")

DEFAULT_PASSWORDS = (ADMIN_PASSWORD == "admin123" or STAFF_PASSWORD == "staff123")
