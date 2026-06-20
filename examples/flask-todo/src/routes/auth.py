"""Authentication routes"""
from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from werkzeug.security import check_password_hash
from urllib.parse import urlparse
from ..forms import LoginForm, RegisterForm
from ..models import create_user, get_user_by_email

auth_bp = Blueprint("auth", __name__)

def is_safe_url(target):
    """B-003 FIXED: Only allow redirects to same site (relative URLs or same netloc)"""
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(target)
    # Allow relative URLs (no netloc) or URLs with same netloc
    return test_url.netloc == "" or test_url.netloc == ref_url.netloc

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        # B-006 FIXED: Check for duplicate email before insert
        if get_user_by_email(form.email.data):
            flash("This email is already registered.")
            return render_template("register.html", form=form)
        create_user(form.email.data, form.password.data)
        return redirect(url_for("auth.login"))
    return render_template("register.html", form=form)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = get_user_by_email(form.email.data)
        if user:
            pw = user["password"]
            authenticated = False
            # B-007 FIXED: Handle plaintext→hash migration for pre-B-002 users
            if pw.startswith("scrypt:"):
                authenticated = check_password_hash(pw, form.password.data)
            elif pw == form.password.data:
                # Plaintext password from before B-002 — upgrade to hash
                from ..models import upgrade_password
                upgrade_password(user["id"], form.password.data)
                authenticated = True

            if authenticated:
                session["user_id"] = user["id"]
                next_page = request.args.get("next")
                if next_page and is_safe_url(next_page):
                    return redirect(next_page)
                return redirect(url_for("todos.list_todos"))
            flash("Invalid email or password.")
    return render_template("login.html", form=form)

@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("auth.login"))
