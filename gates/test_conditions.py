"""Tests for ConditionEvaluator."""
import pytest
from multiagent.core.conditions import ConditionEvaluator, ConditionSyntaxError


class TestConditionEvaluator:
    def setup_method(self):
        self.eval = ConditionEvaluator()

    def test_simple_equality(self):
        assert self.eval.evaluate("verdict == 'approved'", {"verdict": "approved"})
        assert not self.eval.evaluate("verdict == 'approved'", {"verdict": "rejected"})

    def test_not_equal(self):
        assert self.eval.evaluate("verdict != 'rejected'", {"verdict": "approved"})

    def test_and_condition(self):
        ctx = {"verdict": "approved", "complexity": "low"}
        assert self.eval.evaluate("verdict == 'approved' and complexity == 'low'", ctx)
        assert not self.eval.evaluate("verdict == 'approved' and complexity == 'high'", ctx)

    def test_or_condition(self):
        assert self.eval.evaluate("x == 'a' or y == 'b'", {"x": "a", "y": "c"})
        assert not self.eval.evaluate("x == 'a' or y == 'b'", {"x": "c", "y": "d"})

    def test_in_condition(self):
        assert self.eval.evaluate("module in ['auth', 'api']", {"module": "auth"})
        assert not self.eval.evaluate("module in ['auth', 'api']", {"module": "db"})

    def test_not_in_condition(self):
        assert self.eval.evaluate("env not in ['prod']", {"env": "dev"})

    def test_numeric_comparisons(self):
        assert self.eval.evaluate("count > 0", {"count": 5})
        assert self.eval.evaluate("count >= 5", {"count": 5})
        assert self.eval.evaluate("count < 10", {"count": 5})

    def test_missing_key_returns_false(self):
        assert not self.eval.evaluate("missing == 'x'", {})

    def test_empty_condition_returns_true(self):
        assert self.eval.evaluate("", {"x": "y"})

    def test_validate_valid(self):
        ok, err = self.eval.validate("verdict == 'ok'")
        assert ok
        assert err is None

    def test_validate_invalid(self):
        ok, err = self.eval.validate("verdict ==")
        assert not ok
        assert err is not None

    def test_nested_and_or(self):
        ctx = {"a": "1", "b": "2", "c": "3"}
        assert self.eval.evaluate("a == '1' and (b == '2' or c == 'x')", ctx)
