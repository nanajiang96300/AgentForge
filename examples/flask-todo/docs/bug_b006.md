# B-006: Duplicate email registration crashes with 500

## Symptom
Registering with an already-used email causes:
```
sqlite3.IntegrityError: UNIQUE constraint failed: users.email
```
The app crashes with a 500 Internal Server Error instead of showing "Email already registered".

## Steps to reproduce
1. Register with email "test@test.com"
2. Register again with email "test@test.com"
3. Crash → 500 error page

## Expected behavior
Show a friendly flash message: "This email is already registered" and redirect back to register page.
