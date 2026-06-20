"""Todo CRUD routes"""
from flask import Blueprint, render_template, request, redirect, url_for, session
from ..forms import TodoForm
from ..models import create_todo, get_todos, delete_todo

todos_bp = Blueprint("todos", __name__)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated

@todos_bp.route("/")
@login_required
def list_todos():
    # BUG B-005: get_todos() returns ALL users' todos
    # Should filter by session["user_id"]
    todos = get_todos()
    form = TodoForm()
    return render_template("todos.html", todos=todos, form=form)

@todos_bp.route("/add", methods=["POST"])
@login_required
def add_todo():
    form = TodoForm()
    if form.validate_on_submit():
        create_todo(session["user_id"], form.title.data)
    return redirect(url_for("todos.list_todos"))

@todos_bp.route("/delete/<int:todo_id>", methods=["POST"])
@login_required
def delete_todo_route(todo_id):
    # BUG B-001: No ownership check before delete
    delete_todo(todo_id)
    return redirect(url_for("todos.list_todos"))
