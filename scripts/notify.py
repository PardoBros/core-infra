import os
import argparse  # Added to read the arguments from YAML
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
}

# --- DISCORD API HELPERS (Bot Mode) ---
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

    # 2. Send Message to that Channel
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
    # 1. Parse Arguments (To get the mapping securely)
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping-b64", help="Base64 encoded user mapping string")
    args = parser.parse_args()

    # 2. Load User Mapping
    user_map = {}
    if args.mapping_b64:
        try:
            # Decode the argument passed from YAML
            decoded_json = base64.b64decode(args.mapping_b64).decode("utf-8")
            user_map = json.loads(decoded_json)
            print(f"✅ Loaded mapping for {len(user_map)} users.")
        except Exception as e:
            print(f"⚠️ Error decoding User Mapping: {e}")
            user_map = {}
    else:
        # Fallback: Check environment variable if arg is missing
        print("⚠️ No --mapping-b64 argument. Checking env vars...")
        try:
            mapping_b64_env = os.environ.get("USER_MAPPING", "").strip()
            if mapping_b64_env:
                user_map = json.loads(base64.b64decode(mapping_b64_env).decode("utf-8"))
        except:
            pass

    # 3. Load GitHub Event Data
    if "GITHUB_EVENT_PATH" not in os.environ:
        print("❌ Error: GITHUB_EVENT_PATH not set. Are you running locally without mocking?")
        return

    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        event = json.load(f)

    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    action = event.get("action")
    print(f"🔍 Event: {event_name} | Action: {action}")

    # --- BUILD CONTEXT ---
    data = {}

    # CASE: PULL REQUEST
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
            "draft": pr.get("draft", False),
            "merged": pr.get("merged", False)
        }

    # CASE: COMMENT (Issue or Review)
    elif event_name in ["issue_comment", "pull_request_review_comment"]:
        # Safety check: Ensure it's a PR comment, not a regular issue comment
        if event_name == "issue_comment" and "pull_request" not in event["issue"]:
            print("Skipping: Comment is on a regular Issue, not a PR.")
            exit(0)

        comment = event["comment"]
        # Handle difference in payload structure between issue_comment and review_comment
        pr_source = event["pull_request"] if "pull_request" in event else event["issue"]

        data = {
            "type": "comment",
            "title": pr_source["title"],
            "url": comment["html_url"],
            "repo": event["repository"]["full_name"],
            "sender": comment["user"]["login"],
            "avatar": comment["user"]["avatar_url"],
            "author": pr_source["user"]["login"],
            "head": "", "base": "", # Comments don't usually imply branch changes
            "body": comment["body"]
        }
    else:
        print(f"Skipping unsupported event: {event_name}")
        exit(0)

    # --- LOGIC: BUILD EMBED & RECIPIENTS ---
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

    # 1. REVIEW REQUESTED -> Notify Reviewer
    if event_name == "pull_request" and action == "review_requested":
        if "requested_reviewer" in event:
            recipients.append(event["requested_reviewer"]["login"])
            embed["color"] = COLORS["INFO"]
            embed["description"] = "**Review Requested**\nYou were requested to review this PR."

    # 2. ASSIGNED -> Notify Assignee
    elif event_name == "pull_request" and action == "assigned":
        if "assignee" in event and event["assignee"]:
             recipients.append(event["assignee"]["login"])
             embed["color"] = COLORS["INFO"]
             embed["description"] = "**Assigned to You**\nYou were assigned to this PR."

    # 3. MERGED / CLOSED -> Notify PR Author
    elif event_name == "pull_request" and action == "closed":
        if data["author"] != data["sender"]:
            recipients.append(data["author"])

        if data["merged"]:
            embed["color"] = COLORS["MERGED"]
            embed["description"] = "**Your PR was Merged!**"
        else:
            embed["color"] = COLORS["CLOSED"]
            embed["description"] = "**Your PR was Closed** (Unmerged)"

    # 4. COMMENTS -> Notify PR Author OR Mentioned Users
    elif data.get("type") == "comment":
        embed["color"] = COLORS["COMMENT"]
        embed["description"] = "**New Comment**"
        # Truncate long comments
        body_preview = data["body"][:200] + "..." if len(data["body"]) > 200 else data["body"]
        embed["fields"].append({"name": "Message", "value": body_preview, "inline": False})

        # Notify Author (if they didn't write the comment)
        if data["author"] != data["sender"]:
            recipients.append(data["author"])

        # Notify @Mentions
        mentions = re.findall(r"@([a-zA-Z0-9-]+)", data["body"])
        recipients.extend(mentions)

    # --- SEND LOOP ---
    unique_recipients = list(set(recipients))

    if not unique_recipients:
        print("ℹ️ No relevant users to DM.")

    for gh_user in unique_recipients:
        # Don't notify the person who triggered the action (Sender)
        if gh_user == data["sender"]:
            continue

        discord_id = user_map.get(gh_user)

        if discord_id:
            print(f"🚀 Sending DM to GitHub user: {gh_user} (Discord: {discord_id})")
            send_dm(discord_id, embed)
        else:
            print(f"⚠️ Skipping {gh_user}: No Discord ID found in mapping.")

if __name__ == "__main__":
    main()