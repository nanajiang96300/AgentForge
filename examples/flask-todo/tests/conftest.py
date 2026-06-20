"""Pytest fixtures"""
import pytest, os, tempfile
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
import sys; sys.path.insert(0, ".")

from src import create_app

@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-key"
    app.config["DATABASE"] = db_path
    app.config["WTF_CSRF_ENABLED"] = False  # Disable CSRF for API testing
    with app.app_context():
        from src.db import init_db
        init_db()
    yield app
    try: os.unlink(db_path)
    except: pass

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def auth_user(client):
    client.post("/register", data={"email": "a@test.com", "password": "pass123"})
    client.post("/login", data={"email": "a@test.com", "password": "pass123"})
    return {"email": "a@test.com", "password": "pass123"}
