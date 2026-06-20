# Bugs Found During Testing

## B-001: Delete todo lacks ownership verification
- **File**: src/models.py, delete_todo()
- **Symptom**: User A can delete User B's todo by guessing the todo ID
- **Expected**: Only the owner of a todo can delete it. Non-owner gets 403.
- **Severity**: High

## B-002: Password stored in plaintext
- **File**: src/models.py, create_user()
- **Symptom**: Passwords saved as-is in SQLite. Anyone with DB access can read all passwords.
- **Expected**: Password should be hashed with werkzeug.security.generate_password_hash()
- **Severity**: High

## B-003: Open redirect in login
- **File**: src/routes/auth.py, login()
- **Symptom**: ?next=https://evil.com redirects to external site after login
- **Expected**: Only allow redirects to relative paths or whitelisted domains
- **Severity**: Medium

## B-004: CSRF protection disabled
- **File**: src/forms.py, all form classes
- **Symptom**: Forms accept submissions from any origin without CSRF token
- **Expected**: Enable CSRF (csrf = True) and include {{ form.hidden_tag() }} in templates
- **Severity**: Medium

## B-005: Data leak between users
- **File**: src/models.py, get_todos()
- **Symptom**: All users see ALL todos regardless of ownership
- **Expected**: Filter by session["user_id"]
- **Severity**: High
