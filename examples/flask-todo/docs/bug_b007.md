# B-007: Existing users cannot login after password hashing migration

## Symptom
After B-002 fix (password hashing with werkzeug), users who registered BEFORE the fix cannot login. Their passwords were stored as plaintext, but login now uses `check_password_hash()` which expects scrypt: format.

## Steps to reproduce
1. Start app before B-002 fix → register "old@user.com" with password "oldpass"
2. Apply B-002 fix (password hashing)
3. Try to login as "old@user.com" → no response, login fails silently

## Root cause
Data migration gap: `check_password_hash()` fails on plaintext passwords (doesn't have scrypt: prefix). No fallback logic to handle existing plaintext passwords.

## Expected behavior
Login should auto-migrate: detect plaintext password → verify directly → upgrade to hash on successful login.
