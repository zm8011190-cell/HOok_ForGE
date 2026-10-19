import hashlib
import hmac
import socket
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.models.event import DeliveryStatus
from app.services.dispatcher import calculate_backoff_time, generate_signature
from app.services.security import validate_webhook_url


class WebhookLogicTests(unittest.TestCase):
    def test_signature_uses_hmac_sha256(self):
        expected = hmac.new(b"secret", b"payload", hashlib.sha256).hexdigest()

        self.assertEqual(generate_signature("payload", "secret"), expected)

    def test_backoff_returns_aware_time_with_jitter(self):
        before = datetime.now(timezone.utc)

        retry_at = calculate_backoff_time(1)

        delay = (retry_at - before).total_seconds()
        self.assertIsNotNone(retry_at.tzinfo)
        self.assertGreaterEqual(delay, 2)
        self.assertLessEqual(delay, 3.01)

    def test_url_validation_rejects_private_addresses(self):
        resolutions = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch(
            "app.services.security.socket.getaddrinfo", return_value=resolutions
        ):
            with self.assertRaises(HTTPException):
                validate_webhook_url("https://hooks.example.com")

    def test_url_validation_accepts_public_addresses(self):
        resolution = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        with patch(
            "app.services.security.socket.getaddrinfo", return_value=resolution
        ):
            validate_webhook_url("https://hooks.example.com")

    def test_delivery_status_includes_processing_lease(self):
        self.assertEqual(DeliveryStatus.PROCESSING.value, "PROCESSING")


if __name__ == "__main__":
    unittest.main()
