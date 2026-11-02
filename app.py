import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

app = Flask(__name__)

PAYLOAD_DIR = os.path.join(os.path.dirname(__file__), "payloads")
os.makedirs(PAYLOAD_DIR, exist_ok=True)

USE_TEST_ENV = os.environ.get("USE_TEST_ENV", "false").lower() == "true"
_ENV_PREFIX = "TEST_" if USE_TEST_ENV else "ASURINT_"


def _env(name: str) -> str:
    return os.environ.get(f"{_ENV_PREFIX}{name}", "")


AGENT_RUN_API_BASE_URL = _env("AGENT_RUN_API_BASE_URL")
ROOT_LENS_AGENT_ID = _env("ROOT_LENS_AGENT_ID")
ROOT_LENS_AGENT_NAME = _env("ROOT_LENS_AGENT_NAME")
AUTH_TOKEN_URL = _env("AUTH_TOKEN_URL")
AUTH_CLIENT_ID = _env("AUTH_CLIENT_ID")
AUTH_CLIENT_SECRET = _env("AUTH_CLIENT_SECRET")
AUTH_API_KEY = _env("AUTH_API_KEY")
AUTH_TENANT_ID = _env("AUTH_TENANT_ID")

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")


def save_payload(payload: dict) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = os.path.join(PAYLOAD_DIR, f"{timestamp}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path


def get_access_token() -> Optional[str]:
    if not (AUTH_TOKEN_URL and AUTH_CLIENT_ID and AUTH_CLIENT_SECRET):
        return None
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": AUTH_CLIENT_ID,
            "client_secret": AUTH_CLIENT_SECRET,
        }
    ).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if AUTH_TENANT_ID:
        headers["X-Tenant-ID"] = AUTH_TENANT_ID
    if AUTH_API_KEY:
        headers["X-API-Key"] = AUTH_API_KEY
    req = urllib.request.Request(AUTH_TOKEN_URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_response = json.loads(resp.read().decode())
    return token_response["access_token"]


def build_multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----FormBoundary" + hashlib.sha256(
        json.dumps(fields, sort_keys=True).encode()
    ).hexdigest()[:16]
    lines: list[str] = []
    for name, value in fields.items():
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="{name}"')
        lines.append("")
        lines.append(value)
    lines.append(f"--{boundary}--")
    return "\r\n".join(lines).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def call_root_lens_agent(agent_params: dict) -> None:
    """Trigger the RootLens agent run for a Slack bot-mention event.

    Runs in dry-run mode (logs the request instead of sending it) until
    AGENT_RUN_API_BASE_URL, ROOT_LENS_AGENT_ID and ROOT_LENS_AGENT_NAME are configured.
    """
    fields = {
        "agent_name": ROOT_LENS_AGENT_NAME,
        "id": ROOT_LENS_AGENT_ID,
        "run_in_sync": "false",
        "agent_params": json.dumps(agent_params),
    }

    if not (AGENT_RUN_API_BASE_URL and ROOT_LENS_AGENT_ID and ROOT_LENS_AGENT_NAME):
        print("[call_root_lens_agent] DRY RUN (agent API not configured) — would POST:")
        print(f"  url    = <AGENT_RUN_API_BASE_URL>/api/v1/agent/run")
        print(f"  fields = {fields}")
        return

    url = f"{AGENT_RUN_API_BASE_URL}/api/v1/agent/run"
    body, content_type = build_multipart(fields)
    headers = {"Content-Type": content_type}
    if AUTH_TENANT_ID:
        headers["X-Tenant-ID"] = AUTH_TENANT_ID
    if AUTH_API_KEY:
        headers["X-API-Key"] = AUTH_API_KEY
    token = get_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, method="POST", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"[call_root_lens_agent] agent run created: {result}")
    except urllib.error.HTTPError as e:
        print(f"[call_root_lens_agent] agent_run_api returned {e.code}: {e.read().decode()}")


def verify_slack_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not SLACK_SIGNING_SECRET:
        return False
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    computed = (
        "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(computed, signature)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/webhook/slack", methods=["POST"])
def slack_webhook():
    raw_body = request.get_data()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(raw_body, timestamp, signature):
        print("[slack webhook] invalid signature")
        return jsonify({"error": "invalid signature"}), 401

    payload = request.get_json(silent=True) or {}

    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge")})

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        print(f"[slack webhook] event_type={event.get('type')} payload={payload}")
        saved_path = save_payload(payload)
        print(f"  saved payload -> {saved_path}")

        if event.get("type") == "app_mention":
            agent_params = {
                "text": event.get("text", ""),
                "channel": event.get("channel", ""),
                "user": event.get("user", ""),
            }
            call_root_lens_agent(agent_params)

    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
