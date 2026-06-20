"""Flask TODO App"""
import os
from flask import Flask
from .db import init_db, close_db

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-insecure")
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    from .routes.auth import auth_bp
    from .routes.todos import todos_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(todos_bp)

    return app
