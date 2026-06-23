"""Tests for Slack notification module."""

from unittest.mock import patch, MagicMock

from labquery.notify import SlackNotifier


class TestSlackNotifier:
    def test_disabled_by_default(self):
        n = SlackNotifier()
        assert not n.enabled
        assert n.send("hello") is False

    def test_enabled_with_url(self):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert n.enabled

    @patch("labquery.notify.httpx.post")
    def test_send_posts_to_webhook(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert n.send("hello") is True
        mock_post.assert_called_once_with(
            "https://hooks.slack.com/test",
            json={"text": "hello"},
            timeout=5,
        )

    @patch("labquery.notify.httpx.post")
    def test_notify_run_completed(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert n.notify_run_completed("RUN-123", "serial_dilution", 5, 2.5) is True
        payload = mock_post.call_args[1]["json"]["text"]
        assert "RUN-123" in payload
        assert "serial_dilution" in payload

    @patch("labquery.notify.httpx.post")
    def test_notify_run_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert n.notify_run_error("RUN-456", "cel_dna_combination", "insufficient volume") is True
        payload = mock_post.call_args[1]["json"]["text"]
        assert "RUN-456" in payload
        assert "insufficient volume" in payload

    @patch("labquery.notify.httpx.post")
    def test_notify_measurement(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert n.notify_measurement(["S1", "S2"], 0.4321) is True
        payload = mock_post.call_args[1]["json"]["text"]
        assert "S1" in payload
        assert "0.4321" in payload

    @patch("labquery.notify.httpx.post", side_effect=Exception("connection refused"))
    def test_send_handles_error_gracefully(self, mock_post):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        assert n.send("hello") is False

    def test_stub_methods_noop_when_disabled(self):
        n = SlackNotifier()
        assert n.notify_run_completed("RUN-1", "test", 1, 1.0) is False
        assert n.notify_run_error("RUN-1", "test", "err") is False
        assert n.notify_measurement(["S1"], 0.5) is False
