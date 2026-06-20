"""Todo CRUD tests"""
def test_list_empty(client, auth_user):
    client.post("/login", data=auth_user)
    r = client.get("/")
    assert r.status_code == 200

def test_add_todo(client, auth_user):
    client.post("/login", data=auth_user)
    r = client.post("/add", data={"title": "Test Todo"}, follow_redirects=True)
    assert r.status_code == 200
    assert b"Test Todo" in r.data

def test_delete_own_todo(client, auth_user):
    client.post("/login", data=auth_user)
    client.post("/add", data={"title": "Delete Me"})
    r = client.post("/delete/1", follow_redirects=True)
    assert r.status_code == 200
