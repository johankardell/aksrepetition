import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

import psycopg
import uvicorn
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage, TransportType
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from opentelemetry import propagate, trace
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("orders")
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor()

api = FastAPI(title="AKS enterprise orders lab")
tracer = trace.get_tracer("orders")
requests = Counter("orders_http_requests_total", "HTTP requests", ["method", "status"])
latency = Histogram("orders_http_duration_seconds", "HTTP latency")
credential = DefaultAzureCredential()
stopping = threading.Event()


class Order(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    item: str = Field(min_length=1, max_length=200)


def bus_client():
    return ServiceBusClient(
        fully_qualified_namespace=os.environ["SERVICEBUS_NAMESPACE"],
        credential=credential,
        transport_type=TransportType.AmqpOverWebsocket,
    )


def database():
    token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default").token
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        dbname=os.getenv("POSTGRES_DATABASE", "ordersdb"),
        user=os.environ["POSTGRES_USER"],
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        password=token,
        sslmode="verify-full",
        sslrootcert="/etc/ssl/certs/ca-certificates.crt",
        connect_timeout=10,
    )


def persist_order(order, seen):
    if os.getenv("POSTGRES_HOST"):
        with database() as connection:
            result = connection.execute(
                "INSERT INTO processed_orders(order_id,item) VALUES (%s,%s) "
                "ON CONFLICT (order_id) DO NOTHING RETURNING order_id",
                (order.id, order.item),
            ).fetchone()
            existing = connection.execute(
                "SELECT item FROM processed_orders WHERE order_id=%s", (order.id,)
            ).fetchone()
            if existing[0] != order.item:
                raise ValueError("Order ID already exists with a different item")
            return result is not None
    # Before lab 8 this is deliberately process-local, not durable exactly-once processing.
    if order.id in seen:
        if seen[order.id] != order.item:
            raise ValueError("Order ID already exists with a different item")
        return False
    seen[order.id] = order.item
    return True


@api.middleware("http")
async def observe(request: Request, call_next):
    start = time.monotonic()
    status = "500"
    try:
        response = await call_next(request)
        status = str(response.status_code)
        return response
    finally:
        if request.url.path not in ("/metrics", "/healthz", "/readyz"):
            requests.labels(request.method, status).inc()
            latency.observe(time.monotonic() - start)


@api.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><html lang="en"><meta charset="utf-8">
    <title>Enterprise orders API</title><h1>Enterprise orders API</h1>
    <p>Use <a href="/docs">the API explorer</a> to submit synthetic orders.</p>
    <p>This lab endpoint has no end-user authentication. Do not expose real data.</p></html>"""


@api.get("/healthz")
def health():
    return {"status": "alive"}


@api.get("/readyz")
def ready():
    if os.getenv("FAIL_READINESS") == "true":
        raise HTTPException(503, "Injected readiness failure")
    return {"status": "ready", "dependency_check": "not included"}


@api.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@api.get("/config-version")
def config_version():
    path = Path(os.getenv("CONFIG_FILE", "/mnt/secrets-store/lab-version"))
    if not path.is_file():
        raise HTTPException(503, "CSI configuration not mounted")
    # Only the synthetic lab-version value is exposed; never point this at a real secret.
    return {"lab_version": path.read_text().strip()}


@api.post("/orders", status_code=202)
def submit(order: Order):
    with tracer.start_as_current_span("orders.enqueue"):
        carrier = {}
        propagate.inject(carrier)
        with bus_client() as client, client.get_queue_sender(
            queue_name=os.getenv("QUEUE_NAME", "orders")
        ) as sender:
            sender.send_messages(ServiceBusMessage(
                order.model_dump_json(), message_id=order.id,
                application_properties=carrier,
            ))
    return {"id": order.id, "status": "accepted", "processed": False}


@api.get("/orders/{order_id}")
def get_order(order_id: str):
    if not os.getenv("POSTGRES_HOST"):
        raise HTTPException(503, "Durable order lookup is enabled in lab 8")
    with database() as connection:
        row = connection.execute(
            "SELECT order_id,item,processed_at FROM processed_orders WHERE order_id=%s",
            (order_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Order not processed")
    return {"id": row[0], "item": row[1], "processed_at": row[2]}


def run_worker():
    seen = {}
    delay = float(os.getenv("PROCESSING_DELAY_SECONDS", "0"))
    if delay < 0 or delay > 30:
        raise ValueError("PROCESSING_DELAY_SECONDS must be between 0 and 30")
    with bus_client() as client, client.get_queue_receiver(
        queue_name=os.getenv("QUEUE_NAME", "orders"),
        max_wait_time=5,
        prefetch_count=0,
    ) as receiver:
        while not stopping.is_set():
            for message in receiver.receive_messages(max_message_count=1, max_wait_time=5):
                raw_carrier = message.application_properties or {}
                carrier = {
                    (key.decode() if isinstance(key, bytes) else key):
                    (value.decode() if isinstance(value, bytes) else value)
                    for key, value in raw_carrier.items()
                }
                with tracer.start_as_current_span("orders.process", context=propagate.extract(carrier)):
                    try:
                        order = Order.model_validate_json(str(message))
                        if stopping.wait(delay):
                            receiver.abandon_message(message)
                            return
                        inserted = persist_order(order, seen)
                    except ValueError:
                        logger.exception("Invalid order; sending to dead-letter queue")
                        receiver.dead_letter_message(message, reason="InvalidOrder")
                        continue
                    # Storage and transport failures terminate the worker and leave the lock
                    # uncompleted: Kubernetes restarts it and Service Bus redelivers.
                    receiver.complete_message(message)
                    logger.info(json.dumps({
                        "event": "order_processed", "order_id": order.id,
                        "duplicate": not inserted, "durable": bool(os.getenv("POSTGRES_HOST")),
                    }))


if __name__ == "__main__":
    role = os.getenv("ROLE", "api")
    if role not in ("api", "worker"):
        raise ValueError("ROLE must be api or worker")
    if role == "worker":
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        signal.signal(signal.SIGINT, lambda *_: stopping.set())
        run_worker()
    else:
        uvicorn.run(api, host="0.0.0.0", port=8080)
