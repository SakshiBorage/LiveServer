import json
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)

PAYLOAD_DIR = os.path.join(os.path.dirname(__file__), "payloads")
os.makedirs(PAYLOAD_DIR, exist_ok=True)


def save_payload(payload: dict) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = os.path.join(PAYLOAD_DIR, f"{timestamp}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path


def handle_issue_created(payload: dict) -> None:
    """Extension point: whatever should run when a Jira issue is created.

    Currently just logs + saves the raw payload so we can inspect its shape.
    """
    issue = payload.get("issue", payload if "key" in payload else {})
    fields = issue.get("fields", {})
    print(
        f"[issue created] key={issue.get('key')} "
        f"summary={fields.get('summary')!r} "
        f"event={payload.get('webhookEvent')}"
    )
    print(json.dumps(payload, indent=2))
    saved_path = save_payload(payload)
    print(f"  saved payload -> {saved_path}")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/webhook/jira", methods=["POST"])
def jira_webhook():
    payload = request.get_json(silent=True) or {}
    handle_issue_created(payload)
    return jsonify({"status": "received"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
