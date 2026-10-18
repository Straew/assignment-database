"""Role-based access control."""
import auth


def test_authenticate():
    assert auth.authenticate("admin", "admin123") == auth.ROLE_ADMIN
    assert auth.authenticate(" STAFF ", "staff123") == auth.ROLE_STAFF
    assert auth.authenticate("admin", "wrong") is None
    assert auth.authenticate("ghost", "x") is None
    assert auth.authenticate(None, None) is None


def test_permissions():
    assert auth.can("field_staff", "survey.add")
    assert not auth.can("field_staff", "summary.generate")
    assert not auth.can("field_staff", "partner.manage")
    assert auth.can("admin", "summary.generate") and auth.can("admin", "survey.add")
    assert not auth.can("nobody", "dashboard")
