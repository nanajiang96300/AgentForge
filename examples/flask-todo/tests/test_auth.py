"""Auth endpoint tests"""
def test_register(client):
    r = client.post("/register", data={"email": "test@test.com", "password": "secret"})
    assert r.status_code in (200, 302)

def test_login(client, auth_user):
    r = client.post("/login", data=auth_user, follow_redirects=True)
    assert r.status_code == 200

def test_logout(client, auth_user):
    client.post("/login", data=auth_user)
    r = client.get("/logout", follow_redirects=True)
    assert r.status_code == 200

def test_duplicate_email_graceful(client):
    """B-006 FIXED: Duplicate email should show error, not 500 crash"""
    r1 = client.post("/register", data={"email": "dup@test.com", "password": "p1"}, follow_redirects=True)
    assert r1.status_code == 200
    r2 = client.post("/register", data={"email": "dup@test.com", "password": "p2"}, follow_redirects=True)
    assert r2.status_code == 200  # Not 500
    assert b"already registered" in r2.data.lower()
