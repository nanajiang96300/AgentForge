"""SQLite Database Setup"""
import sqlite3, os
from flask import g, current_app

def get_db_path():
    return current_app.config.get("DATABASE", "todos.db") if current_app else "todos.db"

def get_db():
    if "db" not in g:
        db_path = get_db_path()
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None: db.close()

def init_db():
    db_path = current_app.config.get("DATABASE", "todos.db") if current_app else "todos.db"
    db = sqlite3.connect(db_path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    db.commit()
    db.close()
