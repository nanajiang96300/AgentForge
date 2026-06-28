"""Calculator — minimal TDD implementation."""

from __future__ import annotations


class Calculator:
    """A simple integer calculator with static methods."""

    @staticmethod
    def add(a: int, b: int) -> int:
        """Return the sum of a and b.

        Raises:
            TypeError: If a or b is not an integer.
        """
        if not isinstance(a, int) or not isinstance(b, int):
            raise TypeError("Both arguments must be integers")
        return a + b

    @staticmethod
    def subtract(a: int, b: int) -> int:
        """Return the difference of a minus b.

        Raises:
            TypeError: If a or b is not an integer.
        """
        if not isinstance(a, int) or not isinstance(b, int):
            raise TypeError("Both arguments must be integers")
        return a - b

    @staticmethod
    def multiply(a: int, b: int) -> int:
        """Return the product of a and b.

        Raises:
            TypeError: If a or b is not an integer.
        """
        if not isinstance(a, int) or not isinstance(b, int):
            raise TypeError("Both arguments must be integers")
        return a * b

    @staticmethod
    def divide(a: int, b: int) -> float:
        """Return the quotient of a divided by b.

        Raises:
            TypeError: If a or b is not an integer.
            ValueError: If b is zero.
        """
        if not isinstance(a, int) or not isinstance(b, int):
            raise TypeError("Both arguments must be integers")
        if b == 0:
            raise ValueError("cannot divide by zero")
        return a / b
