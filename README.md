# Jira Webhook Exploration

Minimal Flask receiver for exploring what Jira sends when an issue is created,
and the end-to-end flow of getting that event to your machine.

## Flow

```
Jira issue created
   -> Jira fires webhook (or an Automation rule's "Send web request" action)
   -> POST to your ngrok public HTTPS URL
   -> ngrok tunnels to localhost:5000
   -> Flask app receives POST, logs it, saves the raw JSON to payloads/
   -> Flask returns 200 OK
```

## 1. Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Run the receiver

```bash
python app.py
```

Sanity check: `curl localhost:5000/health` -> `{"status": "ok"}`

## 3. Expose it publicly with ngrok

```bash
ngrok http 5000
```

Copy the `https://xxxx.ngrok-free.app` forwarding URL. Your webhook target is:

```
https://xxxx.ngrok-free.app/webhook/jira
```

Sanity check from outside: `curl https://xxxx.ngrok-free.app/health`

## 4. Register the webhook in Jira

Which path applies depends on your access level.

### Path A — You have Jira site-admin access (Cloud or Data Center)

1. Jira Settings (gear icon) -> System -> WebHooks -> Create a WebHook
2. URL: the ngrok URL from step 3, e.g. `https://xxxx.ngrok-free.app/webhook/jira`
3. Events: check **Issue -> created**
4. Optionally scope it with a JQL filter, e.g. `project = TEST`

### Path B — No site-admin access (project admin is enough)

1. Go to your project -> Project Settings -> Automation -> Create rule
2. Trigger: **Issue created**
3. Action: **Send web request**
   - URL: the ngrok URL from step 3
   - Method: POST
   - Webhook body: "Issue data" (sends the full issue payload) — or a custom
     JSON body using smart values if you want to shape it yourself

## 5. Trigger and observe

1. Create a new issue in a test project in Jira
2. Watch the console running `app.py` — you should see a log line with the
   issue key and summary
3. Check `payloads/` — a new timestamped `.json` file with the full raw
   payload should appear

## 6. Payload shape (fill in after first capture)

Once you've captured a real payload, the key fields to note are typically:

- `webhookEvent` — e.g. `jira:issue_created`
- `issue_event_type_name`
- `timestamp`
- `user` — who triggered the event
- `issue.id`, `issue.key`
- `issue.fields.summary`, `.issuetype`, `.project`, `.reporter`,
  `.assignee`, `.status`, `.created`

Paste a trimmed example here once captured:

```json
{
  "webhookEvent": "",
  "issue": {}
}
```

## Next steps

`handle_issue_created()` in `app.py` is the extension point — once we know
the real payload shape, replace the logging/save logic there with whatever
should actually happen (e.g. call another API, post a Slack message, etc.).
