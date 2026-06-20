"""Authentication routes"""
from flask import Blueprint, render_template, request, redirect, session, url_for
from ..forms import LoginForm, RegisterForm
from ..models import create_user, get_user_by_email

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        # BUG B-002: Password stored in plaintext (no hashing)
        create_user(form.email.data, form.password.data)
        return redirect(url_for("auth.login"))
    return render_template("register.html", form=form)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = get_user_by_email(form.email.data)
        if user and user["password"] == form.password.data:
            session["user_id"] = user["id"]
            # BUG B-003: Unsanitized redirect parameter (open redirect)
            next_page = request.args.get("next")
            if next_page:
                return redirect(next_page)
            return redirect(url_for("todos.list_todos"))
    return render_template("login.html", form=form)

@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("auth.login"))
