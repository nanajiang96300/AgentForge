# Requirements: Secure Multi-User TODO App

## Functional Requirements
1. Users can register with email + password
2. Users can log in / log out  
3. Users can create TODO items
4. Users can view their own TODO list
5. Users can mark TODOs as complete
6. Users can delete their own TODOs

## Non-Functional Requirements
1. Passwords must be stored securely (hashed with bcrypt/scrypt)
2. Users must not see or delete other users' TODOs
3. CSRF protection must be enabled on all forms
4. No open redirect vulnerabilities in login flow
5. Data isolation: each user sees only their own data

## Stack
- Python 3.10+
- Flask (web framework)
- SQLite (database)
- HTML templates (no JavaScript framework required)

## Acceptance Criteria
- [ ] 15+ automated tests passing
- [ ] Security tests verify: ownership check, hashed passwords, CSRF, no open redirect
- [ ] Functional tests verify: register, login, add/delete todo
