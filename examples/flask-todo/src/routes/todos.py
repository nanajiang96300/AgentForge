"""Todo CRUD routes"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
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
    # B-005 FIXED: Pass user_id to filter by owner
    todos = get_todos(user_id=session["user_id"])
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
    # B-001 FIXED: Check ownership before delete
    if not delete_todo(todo_id, user_id=session["user_id"]):
        flash("Not authorized to delete this todo")
    return redirect(url_for("todos.list_todos"))
