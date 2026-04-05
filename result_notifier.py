#!/usr/bin/env python3
"""Monitor GST admission result status and notify via Telegram when published."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

LOGIN_URL = "https://gstadmission.ac.bd/login-with-id"
DASHBOARD_URL = "https://gstadmission.ac.bd/dashboard"
UNPUBLISHED_MARKERS = (
    "will be available after exam",
    "not published",
    "coming soon",
)


@dataclass
class Config:
    applicant_id: str
    password: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_seconds: int
    state_file: Path
    notify_on_unpublished: bool


class GSTResultMonitor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0 Safari/537.36"
                ),
                "Referer": LOGIN_URL,
            }
        )

    def login(self) -> None:
        response = self.session.get(LOGIN_URL, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        token_input = soup.find("input", attrs={"name": "_token"})
        if token_input is None:
            raise RuntimeError("Unable to find CSRF token on login page")

        token_value = token_input.get("value", "")
        if not token_value:
            raise RuntimeError("CSRF token is empty")

        payload = {
            "_token": token_value,
            "applicant_id": self.config.applicant_id,
            "password": self.config.password,
            "signin": "signin",
        }

        logged_in = self.session.post(LOGIN_URL, data=payload, timeout=30, allow_redirects=True)
        logged_in.raise_for_status()

        page_lower = logged_in.text.lower()
        if "logout" not in page_lower and "dashboard" not in page_lower:
            raise RuntimeError("Login failed. Check Applicant ID and Password.")

    def fetch_dashboard_html(self) -> str:
        response = self.session.get(DASHBOARD_URL, timeout=30)
        response.raise_for_status()
        return response.text

    def extract_result_status(self, html: str) -> tuple[bool, str, str]:
        soup = BeautifulSoup(html, "html.parser")

        target_legend = None
        for legend in soup.find_all(["legend", "h3", "h4"]):
            heading = " ".join(legend.stripped_strings).lower()
            if "admission test result" in heading:
                target_legend = legend
                break

        if target_legend is None:
            raw_text = " ".join(soup.stripped_strings)
            return False, "Result section not found on dashboard.", raw_text

        container = target_legend.find_parent("div") or target_legend.parent
        if container is None:
            container = soup

        container_text = " ".join(container.stripped_strings)
        normalized = container_text.lower()
        is_published = not any(marker in normalized for marker in UNPUBLISHED_MARKERS)

        details = self._extract_human_readable_result(container)
        return is_published, details, container_text

    @staticmethod
    def _extract_human_readable_result(container: Any) -> str:
        lines: list[str] = []

        for node in container.find_all(["p", "li", "th", "td", "strong", "span"]):
            value = " ".join(node.stripped_strings)
            if value:
                lines.append(value)

        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            if line not in seen:
                seen.add(line)
                deduped.append(line)

        if not deduped:
            fallback = " ".join(container.stripped_strings)
            if not fallback:
                return "No result text could be extracted."
            return fallback[:600]

        return "\n".join(deduped[:20])


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def send_telegram_message(bot_token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    response = requests.post(url, data=payload, timeout=30)
    telegram_desc = ""
    telegram_ok = response.ok
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}

    if isinstance(response_json, dict):
        telegram_ok = bool(response_json.get("ok", response.ok))
        telegram_desc = str(response_json.get("description", "")).strip()

    if not telegram_ok:
        if telegram_desc:
            raise RuntimeError(f"Telegram API error ({response.status_code}): {telegram_desc}")
        response.raise_for_status()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config(require_portal_credentials: bool = True) -> Config:
    load_dotenv()

    applicant_id = os.getenv("GST_APPLICANT_ID", "").strip()
    password = os.getenv("GST_PASSWORD", "").strip()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    required_values: list[tuple[str, str]] = [
        ("TELEGRAM_BOT_TOKEN", bot_token),
        ("TELEGRAM_CHAT_ID", chat_id),
    ]
    if require_portal_credentials:
        required_values.extend(
            [
                ("GST_APPLICANT_ID", applicant_id),
                ("GST_PASSWORD", password),
            ]
        )

    missing = [name for name, value in required_values if not value]
    if missing:
        raise ValueError("Missing required environment variables: " + ", ".join(missing))

    poll_seconds = int(os.getenv("POLL_SECONDS", "900"))
    state_file = Path(os.getenv("STATE_FILE", "state.json"))
    notify_on_unpublished = env_bool("NOTIFY_ON_UNPUBLISHED", default=False)

    return Config(
        applicant_id=applicant_id,
        password=password,
        telegram_bot_token=bot_token,
        telegram_chat_id=chat_id,
        poll_seconds=poll_seconds,
        state_file=state_file,
        notify_on_unpublished=notify_on_unpublished,
    )


def perform_check(config: Config) -> tuple[str, bool, bool]:
    monitor = GSTResultMonitor(config)
    monitor.login()
    html = monitor.fetch_dashboard_html()
    is_published, details, raw_text = monitor.extract_result_status(html)

    digest = hashlib.sha256(raw_text.encode("utf-8", errors="ignore")).hexdigest()
    state = load_state(config.state_file)

    previous_status = state.get("last_status")
    previous_digest = state.get("last_digest")

    current_status = "published" if is_published else "unpublished"
    has_changed = previous_status != current_status or previous_digest != digest
    sent_message = False

    if is_published and has_changed:
        msg = (
            "GST Result Alert\n"
            f"Applicant ID: {config.applicant_id}\n"
            "Status: Published\n\n"
            f"Result details:\n{details}\n\n"
            f"Dashboard: {DASHBOARD_URL}"
        )
        send_telegram_message(config.telegram_bot_token, config.telegram_chat_id, msg)
        sent_message = True
    elif (not is_published) and config.notify_on_unpublished and previous_status is None:
        msg = (
            "GST Result Watch Started\n"
            f"Applicant ID: {config.applicant_id}\n"
            "Current status: Not published yet."
        )
        send_telegram_message(config.telegram_bot_token, config.telegram_chat_id, msg)
        sent_message = True

    state.update(
        {
            "last_status": current_status,
            "last_digest": digest,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_state(config.state_file, state)

    return current_status, has_changed, sent_message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GST result to Telegram notifier")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--send-test-message",
        action="store_true",
        help="Send a test Telegram message and exit",
    )
    parser.add_argument(
        "--test-message-text",
        default="GST notifier test message: Telegram bot configuration is working.",
        help="Custom message text for --send-test-message",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    config = load_config(require_portal_credentials=not args.send_test_message)

    if args.send_test_message:
        send_telegram_message(
            config.telegram_bot_token,
            config.telegram_chat_id,
            args.test_message_text,
        )
        logging.info("Telegram test message sent successfully")
        return

    try:
        while True:
            try:
                status, changed, sent = perform_check(config)
                logging.info("Checked status=%s changed=%s telegram_sent=%s", status, changed, sent)
            except Exception as exc:  # pylint: disable=broad-except
                logging.exception("Check failed: %s", exc)

            if args.once:
                return

            time.sleep(config.poll_seconds)
    except KeyboardInterrupt:
        logging.info("Stopped by user")
        return


if __name__ == "__main__":
    main()
