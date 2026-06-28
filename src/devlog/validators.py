"""Devlog validators."""


def validate_email(email):
    """Validate email string.

    Args:
        email: String to validate.

    Returns:
        True if email is valid, False otherwise.

    Raises:
        TypeError: If email is None.
    """
    if email is None:
        raise TypeError("email must be a string, got NoneType")
    if email.count("@") != 1:
        return False
    local_part, _, domain_part = email.partition("@")
    if not local_part or not domain_part:
        return False
    return True
