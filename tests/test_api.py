import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.event import Delivery, DeliveryStatus, Endpoint, Event


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)

    def all(self):
        return self.values


class FakeSession:
    def __init__(self):
        self.endpoints = []
        self.events = []
        self.deliveries = []
        self.pending = []
        self.next_ids = {Endpoint: 1, Event: 1, Delivery: 1}

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is Event:
            key = next(
                (
                    value
                    for parameter, value in statement.compile().params.items()
                    if "idempotency_key" in parameter
                ),
                None,
            )
            return next(
                (
                    event
                    for event in self.events
                    if event.idempotency_key == key
                ),
                None,
            )
        return None

    async def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        values = {
            Endpoint: self.endpoints,
            Delivery: self.deliveries,
        }.get(entity, [])
        if entity is Endpoint and statement.whereclause is not None:
            values = [endpoint for endpoint in values if endpoint.is_active]
        return FakeScalars(values)

    async def get(self, model, object_id):
        values = {
            Endpoint: self.endpoints,
            Event: self.events,
            Delivery: self.deliveries,
        }[model]
        return next((value for value in values if value.id == object_id), None)

    def add(self, instance):
        self.pending.append(instance)

    async def commit(self):
        while self.pending:
            instance = self.pending.pop(0)
            model = type(instance)
            instance.id = self.next_ids[model]
            self.next_ids[model] += 1
            if model is Endpoint:
                self.endpoints.append(instance)
            elif model is Event:
                self.events.append(instance)
                for delivery in instance.deliveries:
                    delivery.id = self.next_ids[Delivery]
                    self.next_ids[Delivery] += 1
                    self.deliveries.append(delivery)
            elif model is Delivery:
                self.deliveries.append(instance)

    async def refresh(self, _instance):
        return None

    async def rollback(self):
        self.pending.clear()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeSession()

        async def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health_check(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_dashboard_returns_html(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Hook Forge", response.text)

    def test_register_endpoint_returns_secret_but_list_does_not(self):
        with patch("app.api.endpoints.validate_webhook_url"):
            response = self.client.post(
                "/endpoints", json={"url": "https://hooks.example.com"}
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["url"], "https://hooks.example.com/")
        self.assertTrue(response.json()["is_active"])
        self.assertTrue(response.json()["secret"].startswith("whsec_"))

        listed = self.client.get("/endpoints")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertTrue(listed.json()[0]["is_active"])
        self.assertNotIn("secret", listed.json()[0])

    def test_rejects_invalid_endpoint_url(self):
        with patch(
            "app.api.endpoints.validate_webhook_url",
            side_effect=HTTPException(status_code=400, detail="Private address"),
        ):
            response = self.client.post(
                "/endpoints", json={"url": "http://127.0.0.1"}
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.endpoints, [])

    def test_endpoint_list_returns_inactive_state(self):
        self.db.endpoints.append(
            Endpoint(
                id=1,
                url="https://inactive.example",
                secret="secret",
                is_active=False,
            )
        )

        response = self.client.get("/endpoints")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()[0]["is_active"])

    def test_event_creation_is_idempotent_and_fans_out(self):
        self.db.endpoints.extend(
            [
                Endpoint(
                    id=1, url="https://one.example", secret="secret-1", is_active=True
                ),
                Endpoint(
                    id=2, url="https://two.example", secret="secret-2", is_active=True
                ),
                Endpoint(
                    id=3,
                    url="https://inactive.example",
                    secret="secret-3",
                    is_active=False,
                ),
            ]
        )
        payload = {
            "name": "order.created",
            "payload_json": {"order_id": 7},
            "idempotency_key": "request-123",
        }

        created = self.client.post("/events", json=payload)
        replayed = self.client.post("/events", json=payload)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(created.json(), replayed.json())
        self.assertEqual(len(self.db.events), 1)
        self.assertEqual(len(self.db.deliveries), 2)

    def test_idempotency_key_cannot_be_reused_for_different_event(self):
        original = Event(
            id=1,
            name="order.created",
            payload_json={"order_id": 7},
            idempotency_key="request-123",
        )
        self.db.events.append(original)

        response = self.client.post(
            "/events",
            json={
                "name": "order.updated",
                "payload_json": {"order_id": 7},
                "idempotency_key": "request-123",
            },
        )

        self.assertEqual(response.status_code, 409)

    def test_event_request_validation(self):
        response = self.client.post("/events", json={"name": "missing fields"})

        self.assertEqual(response.status_code, 422)

    def test_delivery_retry_resets_attempts_and_queues_immediately(self):
        delivery = Delivery(
            id=1,
            event_id=1,
            endpoint_id=1,
            status=DeliveryStatus.FAILED,
            delivery_attempts=5,
            secret="secret",
            error_message="Previous failure",
        )
        self.db.deliveries.append(delivery)

        response = self.client.post("/deliveries/1/retry")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(delivery.status, DeliveryStatus.RETRYING)
        self.assertEqual(delivery.delivery_attempts, 0)
        self.assertIsNotNone(delivery.next_retry_at)
        self.assertIsNone(delivery.error_message)

    def test_retry_rejects_missing_delivery(self):
        response = self.client.post("/deliveries/404/retry")

        self.assertEqual(response.status_code, 404)

    def test_retry_rejects_delivered_delivery(self):
        self.db.deliveries.append(
            Delivery(
                id=1,
                event_id=1,
                endpoint_id=1,
                status=DeliveryStatus.DELIVERED,
                delivery_attempts=1,
                secret="secret",
            )
        )

        response = self.client.post("/deliveries/1/retry")

        self.assertEqual(response.status_code, 400)

    def test_list_deliveries_returns_delivery_status(self):
        self.db.deliveries.append(
            Delivery(
                id=1,
                event_id=2,
                endpoint_id=3,
                status=DeliveryStatus.FAILED,
                delivery_attempts=2,
                secret="secret",
                response_status_code=500,
                error_message="Endpoint returned HTTP 500",
            )
        )

        response = self.client.get("/deliveries")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()[0]["status"],
            DeliveryStatus.FAILED.value,
        )
        self.assertEqual(response.json()[0]["delivery_attempts"], 2)
        self.assertNotIn("secret", response.json()[0])


if __name__ == "__main__":
    unittest.main()
