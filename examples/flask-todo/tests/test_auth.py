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
