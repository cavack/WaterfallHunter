import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("waterfall-watchdog")

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_URL = "http://waterfall-backend:8000/api/health"
HEARTBEAT = Path("/tmp/watchdog.heartbeat")


class AlertState:
    def __init__(self):
        self.backend_down = False
        self.lock = threading.Lock()

    def transition(self, down: bool, source: str) -> None:
        # Serialize transitions (including alert delivery) so concurrent
        # monitor/webhook events cannot interleave DOWN/RECOVERED messages.
        with self.lock:
            if self.backend_down == down:
                return
            self.backend_down = down
            state = "DOWN" if down else "RECOVERED"
            logger.warning("Backend state changed to %s via %s", state, source)
            send_alert(
                "🚨 <b>WaterfallHunter backend DOWN</b>\n"
                f"Source: {source}\nNo live order is placed."
                if down
                else "✅ <b>WaterfallHunter backend RECOVERED</b>\n"
                f"Source: {source}\nHealth endpoint is responding."
            )


state = AlertState()


def send_alert(message: str) -> None:
    if not TOKEN or not CHAT_ID:
        logger.warning("Telegram alert skipped: credentials are not configured")
        return
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Telegram alert delivery failed: %s", type(exc).__name__)


class AlertmanagerWebhook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/alerts":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("invalid payload size")
            payload = json.loads(self.rfile.read(length))
            alerts = [
                alert for alert in payload.get("alerts", [])
                if alert.get("labels", {}).get("alertname") == "WaterfallBackendDown"
            ]
            statuses = {alert.get("status") for alert in alerts}
            if "firing" in statuses:
                state.transition(True, "Alertmanager")
            elif alerts and statuses == {"resolved"}:
                state.transition(False, "Alertmanager")
        except (ValueError, json.JSONDecodeError, TypeError):
            self.send_error(400)
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        logger.info("Alertmanager webhook: " + format, *args)


def run_webhook() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8080), AlertmanagerWebhook).serve_forever()


def monitor_loop() -> None:
    failures = 0
    while True:
        try:
            response = httpx.get(API_URL, timeout=10.0)
            response.raise_for_status()
            failures = 0
            state.transition(False, "watchdog")
        except httpx.HTTPError as exc:
            failures += 1
            logger.error("Backend health probe failed (%s): %s", failures, type(exc).__name__)
            if failures >= 3:
                state.transition(True, "watchdog")
        HEARTBEAT.touch()
        time.sleep(30)


threading.Thread(target=run_webhook, daemon=True, name="alertmanager-webhook").start()
monitor_loop()
