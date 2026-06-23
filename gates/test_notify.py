"""
P0 Gate: notify.py — retry, truncate, validate, debounce, i18n

Covers _send_with_retry, _truncate_field, _validate_embed_total,
NotifierStepHook rejection cooldown, and i18n t()/set_language().
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from urllib.request import Request
from urllib.error import URLError


# ── i18n ──


class TestI18n:
    def test_default_language_is_chinese(self):
        from multiagent.notify import get_language, t
        assert get_language() == "zh"
        assert "步骤" in t("started")

    def test_switch_to_english(self):
        from multiagent.notify import set_language, get_language, t
        set_language("en")
        assert get_language() == "en"
        assert "Step" in t("started")
        set_language("zh")  # restore

    def test_unknown_key_returns_key_itself(self):
        from multiagent.notify import t
        assert t("nonexistent_key_xyz") == "nonexistent_key_xyz"

    def test_translation_with_format_kwargs(self):
        from multiagent.notify import t
        result = t("rejection.next", count=2)
        assert "2" in result


# ── Field Truncation ──


class TestTruncateField:
    def test_short_text_passes_through(self):
        from multiagent.notify import _truncate_field
        assert _truncate_field("hello") == "hello"

    def test_long_text_truncated_with_indicator(self):
        from multiagent.notify import _truncate_field
        long_text = "x" * 2000
        result = _truncate_field(long_text)
        assert len(result) <= 1024
        assert "...[truncated]" in result

    def test_custom_limit(self):
        from multiagent.notify import _truncate_field
        result = _truncate_field("hello world, this is a long string", limit=20)
        assert len(result) <= 20
        assert "...[truncated]" in result


# ── Embed Total Validation ──


class TestValidateEmbedTotal:
    def test_embed_within_limit_passes_through(self):
        from multiagent.notify import _validate_embed_total
        embed = {"fields": [{"name": "test", "value": "short"}]}
        result = _validate_embed_total(embed)
        assert result["fields"][0]["value"] == "short"

    def test_exceeding_total_trims_longest_field(self):
        from multiagent.notify import _validate_embed_total
        embed = {
            "fields": [
                {"name": "f1", "value": "x" * 5000},
                {"name": "f2", "value": "y" * 2000},
            ]
        }
        result = _validate_embed_total(embed)
        total = sum(len(f["value"]) for f in result["fields"])
        assert total <= 6000
        assert "...[truncated]" in result["fields"][0]["value"]


# ── Retry Logic ──


class TestSendWithRetry:
    @staticmethod
    def _mock_urlopen(status_code, side_effect=None):
        """Create a mock that works as a context manager for urlopen."""
        if side_effect:
            mock = MagicMock()
            mock.__enter__ = MagicMock(side_effect=side_effect)
            mock.__exit__ = MagicMock(return_value=None)
            return mock
        mock_resp = MagicMock()
        mock_resp.status = status_code
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock_resp)
        mock.__exit__ = MagicMock(return_value=None)
        return mock

    def test_success_on_first_attempt(self):
        from multiagent.notify import _send_with_retry

        with patch("multiagent.notify.urlopen",
                   return_value=self._mock_urlopen(200)):
            status = _send_with_retry(Request("http://fake"), max_retries=3)
            assert status == 200

    def test_retries_on_urlerror_then_succeeds(self):
        from multiagent.notify import _send_with_retry

        mock_success = self._mock_urlopen(200)
        with patch("multiagent.notify.urlopen") as mock_open:
            mock_open.side_effect = [
                URLError("timeout"), URLError("timeout"), mock_success
            ]
            with patch("multiagent.notify._time.sleep", return_value=None):
                status = _send_with_retry(Request("http://fake"), max_retries=3)
                assert status == 200
                assert mock_open.call_count == 3

    def test_all_retries_exhausted_returns_none(self):
        from multiagent.notify import _send_with_retry

        with patch("multiagent.notify.urlopen", side_effect=URLError("fail")):
            with patch("multiagent.notify._time.sleep", return_value=None):
                status = _send_with_retry(Request("http://fake"), max_retries=3)
                assert status is None

    def test_non_200_rate_limited_retries_then_exhausts(self):
        """429 rate limit triggers retry; if all attempts hit 429, returns None."""
        from multiagent.notify import _send_with_retry

        with patch("multiagent.notify.urlopen",
                   return_value=self._mock_urlopen(429)):
            with patch("multiagent.notify._time.sleep", return_value=None):
                # 429 always triggers retry (with longer backoff)
                # With max_retries=3, all 3 attempts hit 429 -> exhaust
                status = _send_with_retry(Request("http://fake"), max_retries=3)
                assert status is None  # All attempts consumed by rate-limit retries


# ── Rejection Debounce ──


class TestRejectionDebounce:
    def test_first_rejection_notifies(self):
        from multiagent.notify import NotifierStepHook
        notifier = Mock()
        hook = NotifierStepHook(
            [notifier], db=None, rejection_cooldown_seconds=30
        )
        hook.on_rejection("task-1", "test_verify", 1)
        assert notifier.call_count == 1

    def test_rapid_second_rejection_debounced(self):
        from multiagent.notify import NotifierStepHook
        notifier = Mock()
        hook = NotifierStepHook(
            [notifier], db=None, rejection_cooldown_seconds=30
        )
        hook.on_rejection("task-1", "test_verify", 1)
        # Second call within cooldown
        hook.on_rejection("task-1", "test_verify", 2)
        assert notifier.call_count == 1  # Still 1, debounced

    def test_different_task_no_debounce(self):
        from multiagent.notify import NotifierStepHook
        notifier = Mock()
        hook = NotifierStepHook(
            [notifier], db=None, rejection_cooldown_seconds=30
        )
        hook.on_rejection("task-1", "test_verify", 1)
        hook.on_rejection("task-2", "test_verify", 1)
        assert notifier.call_count == 2  # Different tasks, both fire

    def test_after_cooldown_fires_again(self):
        from multiagent.notify import NotifierStepHook
        notifier = Mock()
        hook = NotifierStepHook(
            [notifier], db=None, rejection_cooldown_seconds=0  # No cooldown
        )
        hook.on_rejection("task-1", "test_verify", 1)
        hook.on_rejection("task-1", "test_verify", 2)
        assert notifier.call_count == 2  # Both fire with cooldown=0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
