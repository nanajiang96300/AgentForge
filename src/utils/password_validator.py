"""PasswordValidator — validates password strength with scoring."""

from __future__ import annotations


class PasswordValidator:
    """Validates password strength based on 5 rules and returns a score + issues.

    Scoring weights:
        - Length >= 8:        15 points
        - Contains uppercase: 25 points
        - Contains lowercase: 20 points
        - Contains digit:     15 points
        - Contains special:   25 points
        Total max:           100 points

    Validity: score >= 75 AND both uppercase and lowercase present.
    """

    SPECIAL_CHARS: str = "!@#$%^&*(),.?\":{}|<>`~-_=+[];:'/\\ "

    @staticmethod
    def validate_strength(password: str) -> dict:
        """Validate password strength and return score, valid flag, and issues.

        Args:
            password: The password string to validate.

        Returns:
            dict with keys: valid (bool), score (int 0-100), issues (list[str])

        Raises:
            TypeError: If password is None or not a string.
        """
        if password is None:
            raise TypeError("Password cannot be None")
        if not isinstance(password, str):
            raise TypeError("Password must be a string")

        issues: list[str] = []
        score: int = 0

        # Rule 1: Length >= 8 (15 points)
        has_length = len(password) >= 8
        if has_length:
            score += 15
        else:
            issues.append("Too short")

        # Rule 2: Contains uppercase letter (25 points)
        has_upper = any(c.isupper() for c in password)
        if has_upper:
            score += 25
        else:
            issues.append("Missing uppercase")

        # Rule 3: Contains lowercase letter (20 points)
        has_lower = any(c.islower() for c in password)
        if has_lower:
            score += 20
        else:
            issues.append("Missing lowercase")

        # Rule 4: Contains digit (15 points)
        has_digit = any(c.isdigit() for c in password)
        if has_digit:
            score += 15
        else:
            issues.append("Missing digit")

        # Rule 5: Contains special character (25 points)
        has_special = any(c in PasswordValidator.SPECIAL_CHARS for c in password)
        if has_special:
            score += 25
        else:
            issues.append("Missing special char")

        # Valid: score >= 75 AND both letter cases present
        valid = score >= 75 and has_upper and has_lower

        return {"valid": valid, "score": score, "issues": issues}
