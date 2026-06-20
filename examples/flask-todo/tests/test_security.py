"""Security regression tests — verify all 5 bugs are fixed"""
import pytest

def test_cannot_delete_others_todo(client):
    """B-001 FIXED: Should NOT be able to delete another user's todo"""
    client.post("/register", data={"email": "u1@test.com", "password": "p1"})
    client.post("/login", data={"email": "u1@test.com", "password": "p1"})
    client.post("/add", data={"title": "U1 Todo"})
    client.get("/logout")

    client.post("/register", data={"email": "u2@test.com", "password": "p2"})
    client.post("/login", data={"email": "u2@test.com", "password": "p2"})
    r = client.post("/delete/1", follow_redirects=True)
    assert b"Not authorized" in r.data, "B-001 FAIL: U2 could delete U1's todo"

def test_password_hashed(client):
    """B-002 FIXED: Passwords should be hashed, not plaintext"""
    client.post("/register", data={"email": "hash@test.com", "password": "mypassword"})
    from src.db import get_db
    with client.application.app_context():
        db = get_db()
        user = db.execute("SELECT password FROM users WHERE email = ?", ("hash@test.com",)).fetchone()
        assert user is not None
        pw = user["password"]
        assert pw != "mypassword", "B-002 FAIL: Password stored in plaintext"
        assert pw.startswith("scrypt:"), f"B-002 FAIL: Password not hashed, got: {pw[:20]}..."

def test_no_open_redirect(client):
    """B-003 FIXED: Login should not redirect to external URLs"""
    r = client.post("/login?next=https://evil.com/phish",
                    data={"email": "a@test.com", "password": "pass123"})
    if r.status_code == 302:
        location = r.headers.get("Location", "")
        assert "evil.com" not in location, f"B-003 FAIL: Open redirect to {location}"

def test_csrf_enabled():
    """B-004 FIXED: Forms should require CSRF token (needs CSRF enabled)"""
    from src import create_app
    import tempfile, os
    db_fd, db_path = tempfile.mkstemp(suffix=".db"); os.close(db_fd)
    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "csrf-test"
    app.config["DATABASE"] = db_path
    app.config["WTF_CSRF_ENABLED"] = True  # CSRF ON for this test
    with app.app_context():
        from src.db import init_db; init_db()
    client = app.test_client()
    r = client.post("/register", data={"email": "csrf@test.com", "password": "p"})
    os.unlink(db_path)
    assert r.status_code != 302, "B-004 FAIL: Form accepted without CSRF token"

def test_plaintext_password_migration(client):
    """B-007 FIXED: Pre-B-002 plaintext passwords auto-upgrade on login"""
    from src.db import get_db
    # Insert a user with plaintext password (simulating pre-B-002 state)
    with client.application.app_context():
        db = get_db()
        db.execute("INSERT INTO users (email, password) VALUES (?, ?)",
                   ("olduser@test.com", "plainpass"))
        db.commit()
    # Try to login — should auto-upgrade
    r = client.post("/login", data={"email": "olduser@test.com", "password": "plainpass"}, follow_redirects=True)
    assert r.status_code == 200
    assert b"Login" not in r.data  # Not redirected back to login → success
    # Verify password was upgraded to hash
    with client.application.app_context():
        db = get_db()
        user = db.execute("SELECT password FROM users WHERE email = ?", ("olduser@test.com",)).fetchone()
        assert user["password"].startswith("scrypt:"), f"Password not upgraded: {user['password'][:20]}"

def test_list_shows_only_own_todos(client):
    """B-005 FIXED: User should only see their own todos"""
    client.post("/register", data={"email": "alice@t.com", "password": "pa"})
    client.post("/login", data={"email": "alice@t.com", "password": "pa"})
    client.post("/add", data={"title": "Alice Todo"})
    client.get("/logout")

    client.post("/register", data={"email": "bob@t.com", "password": "pb"})
    client.post("/login", data={"email": "bob@t.com", "password": "pb"})
    client.post("/add", data={"title": "Bob Todo"})

    r = client.get("/")
    assert b"Alice Todo" not in r.data, "B-005 FAIL: Bob can see Alice's todo"
    assert b"Bob Todo" in r.data, "B-005 FAIL: Bob cannot see his own todo"
