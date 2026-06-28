"""Password strength validator.

Scoring rules:
    - Length >= 8 characters: 10 points
    - Contains uppercase letter: 20 points
    - Contains lowercase letter: 20 points
    - Contains digit: 25 points
    - Contains special character (!@#$%^&*(): 25 points
    - Total >= 60: valid
"""


class PasswordValidator:
    """Validates password strength based on configurable scoring rules."""

    SPECIAL_CHARS = set("!@#$%^&*(")
    VALID_THRESHOLD = 60

    @staticmethod
    def validate_strength(password: str) -> dict:
        """Validate password strength and return a score with issues.

        Args:
            password: The password string to validate.

        Returns:
            dict with keys:
                - valid (bool): True if score >= 60, False otherwise.
                - score (int): Total strength score (0-100).
                - issues (list[str]): List of human-readable deficiency descriptions.

        Raises:
            TypeError: If password is None.
        """
        if password is None:
            raise TypeError("password must be a string, not None")

        score = 0
        issues = []

        # Length check: 10 points
        if len(password) >= 8:
            score += 10
        else:
            issues.append("Too short")

        # Uppercase check: 20 points
        if any(c.isupper() for c in password):
            score += 20
        else:
            issues.append("Missing uppercase")

        # Lowercase check: 20 points
        if any(c.islower() for c in password):
            score += 20
        else:
            issues.append("Missing lowercase")

        # Digit check: 25 points
        if any(c.isdigit() for c in password):
            score += 25
        else:
            issues.append("Missing digit")

        # Special character check: 25 points
        if any(c in PasswordValidator.SPECIAL_CHARS for c in password):
            score += 25
        else:
            issues.append("Missing special char")

        return {
            "valid": score >= PasswordValidator.VALID_THRESHOLD,
            "score": score,
            "issues": issues,
        }
