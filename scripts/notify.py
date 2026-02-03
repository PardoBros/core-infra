import os
import argparse
import json
import base64
import re
import urllib.request
import time
from datetime import datetime

print("--- SCRIPT STARTED ---")

# --- CONFIGURATION ---
COLORS = {
    "OPENED": 5763719,    # Green
    "MERGED": 10181046,   # Purple
    "CLOSED": 15548997,   # Red
    "INFO": 3447003,      # Blue
    "COMMENT": 16776960,  # Yellow
    "APPROVED": 5763719,  # Green (Same as Open)
    "CHANGES": 15548997   # Red (Same as Closed)
}

# --- DISCORD API HELPERS ---
def send_dm(user_id, embed):
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token or not user_id:
        print(f"⚠️ Missing token or user_id for {user_id}")
        return

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "GitHub-Actions-Bot/1.0"
    }

    # 1. Create DM Channel
    dm_url = "https://discord.com/api/v10/users/@me/channels"
    try:
        req = urllib.request.Request(
            dm_url,
            data=json.dumps({"recipient_id": user_id}).encode("utf-8"),
            headers=headers
        )
        resp = urllib.request.urlopen(req)
        channel_id = json.loads(resp.read().decode())["id"]
    except Exception as e:
        print(f"❌ Could not open DM with {user_id}: {e}")
        return

    # 2. Send Message
    msg_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = {"embeds": [embed]}

    try:
        req = urllib.request.Request(
            msg_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )
        urllib.request.urlopen(req)
        print(f"✅ DM sent to {user_id}")
    except Exception as e:
        print(f"❌ Failed to send message to {user_id}: {e}")

# --- MAIN EXECUTION ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping-b64", help="Base64 encoded user mapping string")
    args = parser.parse_args()

    # Load Mapping
    user_map = {}
    if args.mapping_b64:
        try:
            decoded_json = base64.b64decode(args.mapping_b64).decode("utf-8")
            user_map = json.loads(decoded_json)
        except Exception as e:
            print(f"⚠️ Error decoding User Mapping: {e}")

    # Load Event
    if "GITHUB_EVENT_PATH" not in os.environ:
        print("❌ Error: GITHUB_EVENT_PATH not set.")
        return

    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        event = json.load(f)

    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    action = event.get("action")
    print(f"🔍 Event: {event_name} | Action: {action}")

    # --- BUILD CONTEXT ---
    data = {}

    # 1. Standard Pull Request
    if event_name == "pull_request":
        pr = event["pull_request"]
        data = {
            "type": "pr",
            "title": pr["title"],
            "url": pr["html_url"],
            "repo": event["repository"]["full_name"],
            "sender": event["sender"]["login"],
            "avatar": event["sender"]["avatar_url"],
            "author": pr["user"]["login"],
            "head": pr["head"]["ref"],
            "base": pr["base"]["ref"],
            "merged": pr.get("merged", False)
        }

    # 2. Review Submission (Approvals/Changes) - NEW!
    elif event_name == "pull_request_review":
        pr = event["pull_request"]
        review = event["review"]
        data = {
            "type": "review_submit",
            "title": pr["title"],
            "url": review["html_url"],
            "repo": event["repository"]["full_name"],
            "sender": review["user"]["login"], # The Reviewer
            "avatar": review["user"]["avatar_url"],
            "author": pr["user"]["login"],     # The PR Author (Target)
            "head": pr["head"]["ref"],
            "base": pr["base"]["ref"],
            "state": review["state"].lower()   # approved, changes_requested, commented
        }

    # 3. Comments
    elif event_name in ["issue_comment", "pull_request_review_comment"]:
        if event_name == "issue_comment" and "pull_request" not in event["issue"]:
            exit(0)
        comment = event["comment"]
        pr_source = event["pull_request"] if "pull_request" in event else event["issue"]
        data = {
            "type": "comment",
            "title": pr_source["title"],
            "url": comment["html_url"],
            "repo": event["repository"]["full_name"],
            "sender": comment["user"]["login"],
            "avatar": comment["user"]["avatar_url"],
            "author": pr_source["user"]["login"],
            "head": "", "base": "",
            "body": comment["body"]
        }
    else:
        print(f"Skipping unsupported event: {event_name}")
        exit(0)

    # --- LOGIC: BUILD EMBED ---
    recipients = []

    embed = {
        "title": data["title"],
        "url": data["url"],
        "author": {
            "name": f"{data['sender']} ({event_name.replace('_', ' ').title()})",
            "icon_url": data["avatar"]
        },
        "fields": [
            {"name": "📂 Repo", "value": data["repo"], "inline": True},
            {"name": "👤 Author", "value": data["author"], "inline": True}
        ],
        "footer": {"text": "GitHub Notification"}
    }

    if data["head"]:
         embed["fields"].append({"name": "🌿 Branch", "value": f"`{data['head']}` ➝ `{data['base']}`", "inline": True})

    # --- RULES ---

    # 1. PR APPROVED / CHANGES REQUESTED (New Logic)
    if event_name == "pull_request_review":
        if data["state"] == "approved":
            recipients.append(data["author"])
            embed["color"] = COLORS["APPROVED"]
            embed["description"] = "**✅ PR Approved!**"
            embed["fields"].append({"name": "Reviewer", "value": f"Approved by {data['sender']}", "inline": False})

        elif data["state"] == "changes_requested":
            recipients.append(data["author"])
            embed["color"] = COLORS["CHANGES"]
            embed["description"] = "**⚠️ Changes Requested**"
            embed["fields"].append({"name": "Reviewer", "value": f"{data['sender']} requested changes.", "inline": False})

        else:
            print("Skipping 'commented' review type (handled by comment logic)")

    # 2. REVIEW REQUESTED
    elif event_name == "pull_request" and action == "review_requested":
        if "requested_reviewer" in event:
            recipients.append(event["requested_reviewer"]["login"])
            embed["color"] = COLORS["INFO"]
            embed["description"] = "**Review Requested**\nYou were requested to review this PR."

    # 3. ASSIGNED
    elif event_name == "pull_request" and action == "assigned":
        if "assignee" in event and event["assignee"]:
             recipients.append(event["assignee"]["login"])
             embed["color"] = COLORS["INFO"]
             embed["description"] = "**Assigned to You**"

    # 4. CLOSED / MERGED
    elif event_name == "pull_request" and action == "closed":
        if data["author"] != data["sender"]:
            recipients.append(data["author"])
        embed["color"] = COLORS["MERGED"] if data["merged"] else COLORS["CLOSED"]
        embed["description"] = "**Your PR was Merged!**" if data["merged"] else "**Your PR was Closed** (Unmerged)"

    # 5. COMMENTS
    elif data.get("type") == "comment":
        embed["color"] = COLORS["COMMENT"]
        embed["description"] = "**New Comment**"
        body_preview = data["body"][:200] + "..." if len(data["body"]) > 200 else data["body"]
        embed["fields"].append({"name": "Message", "value": body_preview, "inline": False})

        if data["author"] != data["sender"]:
            recipients.append(data["author"])
        mentions = re.findall(r"@([a-zA-Z0-9-]+)", data["body"])
        recipients.extend(mentions)

    # --- SEND ---
    unique_recipients = list(set(recipients))
    for gh_user in unique_recipients:
        if gh_user == data["sender"]: continue # Don't DM yourself

        discord_id = user_map.get(gh_user)
        if discord_id:
            print(f"🚀 Sending DM to {gh_user} ({discord_id})")
            send_dm(discord_id, embed)
        else:
            print(f"⚠️ Skipping {gh_user}: No Discord ID mapped.")

if __name__ == "__main__":
    main()