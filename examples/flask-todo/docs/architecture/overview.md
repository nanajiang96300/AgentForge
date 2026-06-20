# Flask TODO App Architecture

## Structure
```
src/
  __init__.py       Flask app factory
  db.py             SQLite setup (todos.db)
  models.py         Data access layer
  forms.py          WTForms (Login, Register, Todo)
  routes/
    auth.py         /register, /login, /logout
    todos.py        /, /add, /delete/<id>
  templates/
    base.html, login.html, register.html, todos.html
```

## Known Bugs (for Phase 2 testing)
| ID | File | Description |
|----|------|-------------|
| B-001 | models.py:27 | delete_todo() no ownership check |
| B-002 | routes/auth.py:12 | Password stored in plaintext |
| B-003 | routes/auth.py:23 | Login redirect unsanitized (open redirect) |
| B-004 | forms.py | CSRF disabled on all forms |
| B-005 | models.py:23 | get_todos() returns all users' todos |

## Security issues
- No password hashing → B-002
- No ownership verification on delete → B-001
- Open redirect in login → B-003
- No CSRF protection → B-004
- Data leak between users → B-005
