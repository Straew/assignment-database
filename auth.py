"""
Role-based access control (RBAC) shared by the CLI menu and the Flask app.

Two roles exist:

* ``field_staff`` - records surveys, views equipment/stock alerts, exports survey PDFs
* ``admin``       - everything above plus equipment/partner management,
                    daily summaries, summary PDFs and charts

Every feature is guarded by a *permission name*, so adding a role means editing
one table instead of touching every screen.
"""
import hashlib
import hmac
import os

import config as cfg

ROLE_ADMIN, ROLE_STAFF = "admin", "field_staff"

STAFF_PERMISSIONS = frozenset({
    "dashboard", "survey.add", "survey.view", "survey.pdf", "equipment.view", "alerts.view",
})
ADMIN_PERMISSIONS = STAFF_PERMISSIONS | {
    "equipment.manage", "partner.manage", "summary.view", "summary.generate",
    "summary.pdf", "charts.view",
}
PERMISSIONS = {ROLE_STAFF: STAFF_PERMISSIONS, ROLE_ADMIN: ADMIN_PERMISSIONS}


def can(role, permission):
    """Return True if ``role`` holds ``permission`` (unknown roles hold none)."""
    return permission in PERMISSIONS.get(role, frozenset())


def _hash(password, salt):
    """PBKDF2-HMAC-SHA256 digest of ``password`` (bytes)."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


# Credentials come from .env; only salted hashes are kept in memory.
_SALT = os.urandom(16)
_USERS = {
    "admin": (_hash(cfg.ADMIN_PASSWORD, _SALT), ROLE_ADMIN),
    "staff": (_hash(cfg.STAFF_PASSWORD, _SALT), ROLE_STAFF),
}


def authenticate(username, password):
    """Return the user's role for valid credentials, else ``None`` (constant-time compare)."""
    record = _USERS.get((username or "").strip().lower())
    candidate = _hash(password or "", _SALT)
    if record and hmac.compare_digest(record[0], candidate):
        return record[1]
    return None
