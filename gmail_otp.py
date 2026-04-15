"""Gmail OTP fetcher — read-only Gmail API client.

Commands:
    python gmail_otp.py --auth
        One-time interactive OAuth flow. Opens a browser for consent and
        saves the refresh token to secrets/gmail-token.json.

    python gmail_otp.py --poll [--since-ms MS] [--timeout N] [--sender PATTERN]
        Poll Gmail for a recent JAL OTP message. Prints JSON with the code.

Scope: gmail.readonly only — cannot send, modify, or delete anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

PROJECT_ROOT = Path(__file__).parent
CREDS_PATH = PROJECT_ROOT / "secrets" / "gmail-credentials.json"
TOKEN_PATH = PROJECT_ROOT / "secrets" / "gmail-token.json"


def authenticate() -> dict:
    """Force a new OAuth flow and save the token."""
    if not CREDS_PATH.exists():
        raise FileNotFoundError(
            f"OAuth client credentials not found at {CREDS_PATH}. "
            "Download from Google Cloud Console and save there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    return {"authenticated": True, "token_path": str(TOKEN_PATH)}


def get_service():
    """Load the saved token, refreshing if needed. Errors if never authed."""
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"No token at {TOKEN_PATH}. Run `python gmail_otp.py --auth` first."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            raise RuntimeError(
                "Token invalid and not refreshable. Re-run `python gmail_otp.py --auth`."
            )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _walk_parts(part):
    mime = part.get("mimeType", "")
    if mime.startswith("text/"):
        data = part.get("body", {}).get("data")
        if data:
            try:
                yield base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                pass
    for sub in part.get("parts", []) or []:
        yield from _walk_parts(sub)


def _get_body(message) -> str:
    return "\n".join(_walk_parts(message.get("payload", {})))


def _get_subject(message) -> str:
    for h in message.get("payload", {}).get("headers", []) or []:
        if h.get("name", "").lower() == "subject":
            return h.get("value", "")
    return ""


def _extract_code(text: str) -> str | None:
    """Return the most likely OTP code from text.

    Prefer 4-8 digit sequences near 'code', 'password', 'verification',
    'one-time', or Japanese equivalents. Fall back to first standalone 4-8
    digit run.
    """
    patterns = [
        r"(?:one[\s\-]?time[\s\-]?password|verification[\s\-]?code|"
        r"security[\s\-]?code|auth(?:entication)?[\s\-]?code|"
        r"ワンタイム|認証コード|確認コード|パスワード)"
        r"[^\d]{0,80}(\d{4,8})",
        r"(?:code|password)[^\d]{0,40}(\d{4,8})",
        r"(\d{4,8})[^\d]{0,40}(?:code|password|verification|ワンタイム)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{4,8})(?!\d)", text)
    return m.group(1) if m else None


def poll(since_ms: int, timeout: int, sender: str, interval: int = 3) -> dict:
    service = get_service()
    end = time.time() + timeout
    query = f"from:{sender}"
    last_error = None
    while time.time() < end:
        try:
            resp = service.users().messages().list(
                userId="me", q=query, maxResults=10
            ).execute()
            for msg_meta in resp.get("messages", []) or []:
                msg = service.users().messages().get(
                    userId="me", id=msg_meta["id"], format="full"
                ).execute()
                internal_ms = int(msg.get("internalDate", 0))
                if internal_ms < since_ms:
                    continue
                subject = _get_subject(msg)
                body = _get_body(msg)
                text = f"{subject}\n{msg.get('snippet', '')}\n{body}"
                code = _extract_code(text)
                if code:
                    return {
                        "code": code,
                        "message_id": msg["id"],
                        "received_ms": internal_ms,
                        "subject": subject,
                    }
        except Exception as e:
            last_error = str(e)
        time.sleep(interval)
    raise TimeoutError(
        f"No OTP matching from:{sender} since {since_ms} within {timeout}s"
        + (f" (last error: {last_error})" if last_error else "")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true",
                        help="Run interactive OAuth flow")
    parser.add_argument("--poll", action="store_true",
                        help="Poll Gmail for OTP")
    parser.add_argument("--since-ms", type=int, default=0,
                        help="Only consider messages newer than this unix ms timestamp")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Max seconds to poll")
    parser.add_argument("--sender", default="jal",
                        help="Gmail from: filter (default: jal)")
    args = parser.parse_args()

    try:
        if args.auth:
            result = authenticate()
        elif args.poll:
            since = args.since_ms or int((time.time() - 300) * 1000)
            result = poll(since, args.timeout, args.sender)
        else:
            parser.print_help()
            return 1
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
