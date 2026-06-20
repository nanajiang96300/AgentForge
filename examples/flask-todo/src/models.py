"""Data access layer"""
from werkzeug.security import generate_password_hash, check_password_hash
from .db import get_db

def create_user(email, password):
    """B-002 FIXED: Hash password before storing"""
    db = get_db()
    db.execute("INSERT INTO users (email, password) VALUES (?, ?)",
               (email, generate_password_hash(password)))
    db.commit()

def get_user_by_email(email):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

def get_user_by_id(user_id):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

def create_todo(user_id, title):
    db = get_db()
    db.execute("INSERT INTO todos (user_id, title) VALUES (?, ?)", (user_id, title))
    db.commit()

def get_todos(user_id=None):
    """B-005 FIXED: Filter by user_id. If user_id provided, return only that user's todos."""
    db = get_db()
    if user_id is not None:
        return db.execute("SELECT * FROM todos WHERE user_id = ?", (user_id,)).fetchall()
    return db.execute("SELECT * FROM todos").fetchall()

def delete_todo(todo_id, user_id=None):
    """B-001 FIXED: Verify ownership before delete. Returns True if deleted, False if not authorized."""
    db = get_db()
    if user_id is not None:
        todo = db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if todo is None or todo["user_id"] != user_id:
            return False
    db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    db.commit()
    return True
