"""WTForms definitions"""
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email

# BUG B-004: CSRF disabled on all forms
class LoginForm(FlaskForm):
    class Meta:
        csrf = False  # BUG
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")

class RegisterForm(FlaskForm):
    class Meta:
        csrf = False  # BUG
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Register")

class TodoForm(FlaskForm):
    class Meta:
        csrf = False  # BUG
    title = StringField("Title", validators=[DataRequired()])
    submit = SubmitField("Add")
