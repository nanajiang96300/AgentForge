"""Data access layer"""
from .db import get_db

def create_user(email, password):
    db = get_db()
    db.execute("INSERT INTO users (email, password) VALUES (?, ?)", (email, password))
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

def get_todos():
    """BUG B-005: Returns ALL users' todos instead of filtering by user_id"""
    db = get_db()
    return db.execute("SELECT * FROM todos").fetchall()

def delete_todo(todo_id):
    """BUG B-001: Does not verify todo.user_id == current_user.id"""
    db = get_db()
    db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    db.commit()
