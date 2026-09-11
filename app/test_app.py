import asyncio
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import Order, observe, persist_order, ready, requests
from fastapi import HTTPException
from starlette.requests import Request


class OrdersTests(unittest.TestCase):
    def test_validation_rejects_bad_ids_and_empty_item(self):
        for fields in ({"id": "../bad", "item": "x"}, {"id": "valid", "item": ""}):
            with self.assertRaises(ValidationError):
                Order(**fields)

    @patch.dict(os.environ, {}, clear=True)
    def test_process_local_deduplication(self):
        seen = {}
        order = Order(id="order-1", item="widget")
        self.assertTrue(persist_order(order, seen))
        self.assertFalse(persist_order(order, seen))
        self.assertTrue(persist_order(order, {}))
        with self.assertRaises(ValueError):
            persist_order(Order(id="order-1", item="changed"), seen)

    @patch.dict(os.environ, {"FAIL_READINESS": "true"})
    def test_readiness_injection(self):
        with self.assertRaises(HTTPException) as caught:
            ready()
        self.assertEqual(caught.exception.status_code, 503)

    def test_failed_requests_are_counted_and_still_raise(self):
        request = Request({"type": "http", "method": "POST", "path": "/orders", "headers": []})
        counter = requests.labels("POST", "500")
        before = counter._value.get()

        async def fail(_):
            raise RuntimeError("simulated dependency failure")

        with self.assertRaises(RuntimeError):
            asyncio.run(observe(request, fail))
        self.assertEqual(counter._value.get(), before + 1)


if __name__ == "__main__":
    unittest.main()
