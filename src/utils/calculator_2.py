"""Minimal calculator module — GREEN phase implementation."""


class Calculator:
    """Calculator with static arithmetic operations."""

    @staticmethod
    def add(a, b):
        """Return the sum of a and b."""
        return a + b

    @staticmethod
    def subtract(a, b):
        """Return the difference of a minus b."""
        return a - b

    @staticmethod
    def multiply(a, b):
        """Return the product of a and b."""
        return a * b

    @staticmethod
    def divide(a, b):
        """Return the quotient of a divided by b.

        Raises:
            ValueError: If b is zero.
        """
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
