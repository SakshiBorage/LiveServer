import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

app = Flask(__name__)

PAYLOAD_DIR = os.path.join(os.path.dirname(__file__), "payloads")
os.makedirs(PAYLOAD_DIR, exist_ok=True)

APPROVED_STATUS = os.environ.get("JIRA_APPROVED_STATUS", "Approved")
AGENT_RUN_API_BASE_URL = os.environ.get("AGENT_RUN_API_BASE_URL", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
AUTH_TOKEN_URL = os.environ.get("AUTH_TOKEN_URL", "")
AUTH_CLIENT_ID = os.environ.get("AUTH_CLIENT_ID", "")
AUTH_CLIENT_SECRET = os.environ.get("AUTH_CLIENT_SECRET", "")
AUTH_API_KEY = os.environ.get("AUTH_API_KEY", "")
AUTH_TENANT_ID = os.environ.get("AUTH_TENANT_ID", "")


def save_payload(payload: dict) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = os.path.join(PAYLOAD_DIR, f"{timestamp}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path


ADF_BLOCK_TYPES = {"paragraph", "heading", "listItem", "codeBlock", "blockquote"}


def adf_to_text(node: Any) -> str:
    """Recursively extract plain text from a Jira ADF (Atlassian Document Format) node."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    text = "".join(adf_to_text(child) for child in node.get("content", []))
    if node.get("type") in ADF_BLOCK_TYPES:
        text += "\n"
    return text


def build_agent_params(issue: dict) -> dict:
    fields = issue.get("fields", {})
    summary = fields.get("summary", "") or ""
    description = adf_to_text(fields.get("description", ""))
    raw_text = "\n\n".join(part for part in (summary, description) if part).strip()
    return {"ticket_key": issue.get("key"), "raw_text": raw_text}


def get_transition_status(data: dict[str, Any]) -> str:
    """Extract the destination status name from any Jira webhook payload shape."""
    # Automation "Send web request" custom body
    transition = data.get("transition") or {}
    if transition.get("to_status"):
        return transition["to_status"]

    # Native Jira webhook: status change recorded in changelog
    for item in (data.get("changelog") or {}).get("items", []):
        if item.get("field") == "status" and item.get("toString"):
            return item["toString"]

    # "Issue data" webhook body: current status reflects the just-completed transition
    issue = data.get("issue") or (data if "fields" in data else {})
    status = (issue.get("fields") or {}).get("status") or {}
    return status.get("name", "")


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


def call_agent(issue: dict) -> None:
    """Trigger the aetherion agent run for this webhook event.

    Runs in dry-run mode (logs the request instead of sending it) until
    AGENT_RUN_API_BASE_URL, AGENT_ID and AGENT_NAME are configured.
    """
    agent_params = build_agent_params(issue)
    fields = {
        "agent_name": AGENT_NAME,
        "id": AGENT_ID,
        "run_in_sync": "false",
        "agent_params": json.dumps(agent_params),
    }

    if not (AGENT_RUN_API_BASE_URL and AGENT_ID and AGENT_NAME):
        print("[call_agent] DRY RUN (agent API not configured) — would POST:")
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
            print(f"[call_agent] agent run created: {result}")
    except urllib.error.HTTPError as e:
        print(f"[call_agent] agent_run_api returned {e.code}: {e.read().decode()}")


def handle_webhook(payload: dict) -> None:
    issue = payload.get("issue", payload if "key" in payload else {})
    fields = issue.get("fields", {})
    transition_status = get_transition_status(payload)

    print(
        f"[webhook] key={issue.get('key')} summary={fields.get('summary')!r} "
        f"transition_status={transition_status!r}"
    )
    print(json.dumps(payload, indent=2))
    saved_path = save_payload(payload)
    print(f"  saved payload -> {saved_path}")

    if transition_status.lower() != APPROVED_STATUS.lower():
        print(f"[webhook] ignoring — status {transition_status!r} != {APPROVED_STATUS!r}")
        return

    call_agent(issue)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/webhook/jira", methods=["POST"])
def jira_webhook():
    payload = request.get_json(silent=True) or {}
    handle_webhook(payload)
    return jsonify({"status": "received"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
