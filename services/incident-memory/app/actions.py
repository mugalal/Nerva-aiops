"""Ephemeral review drafts. Only a human-confirmed API request can submit to M4."""
import os
import secrets
from threading import Lock
import time

from .providers import ProviderUnavailable


class ActionDrafts:
    def __init__(self):
        self.items = {}
        self.lock = Lock()

    def prepare(self, args, source, mode):
        with self.lock:
            now = time.monotonic()
            self.items = {key: value for key, value in self.items.items() if value["expires"] > now}
            if len(self.items) >= 100:
                return {"status": "unavailable", "detail": "Too many pending action requests"}
            token = secrets.token_urlsafe(32)
            available = source == "real" and mode == "live" and bool(os.getenv("M5_ACTION_REQUEST_PATH"))
            proposal = {**args.model_dump(), "approval_required": True, "source": source}
            self.items[token] = {"expires": now + 600, "proposal": proposal, "state": "draft"}
            return {"status": "review_required", "proposal": proposal, "draft_id": token,
                    "submission_available": available,
                    "detail": "Accept sends this request to M4 for approval. Decline cancels it. Nothing is submitted before your choice." if available else "Accept is unavailable because M4 is not connected or this is demo mode. You can decline this draft. Nothing was executed."}

    def decline(self, token):
        with self.lock:
            draft = self.items.get(token)
            if not draft or draft["expires"] <= time.monotonic():
                raise ProviderUnavailable("Action draft expired or does not exist; nothing was submitted by this choice")
            if draft["state"] == "declined":
                return {"status": "declined", "submitted": False, "executed": False}
            if draft["state"] != "draft":
                raise ProviderUnavailable("This request was already submitted or its outcome is uncertain; cancellation must be checked with M4")
            draft["state"] = "declined"
            return {"status": "declined", "submitted": False, "executed": False}

    def submit(self, token, provider):
        with self.lock:
            draft = self.items.get(token)
            if not draft or draft["expires"] <= time.monotonic():
                raise ProviderUnavailable("Action draft expired or does not exist; prepare it again")
            if draft["state"] != "draft":
                raise ProviderUnavailable("Request already submitted or its outcome is uncertain; check M4 before retrying")
            path = os.getenv("M5_ACTION_REQUEST_PATH")
            if provider.mode != "live" or draft["proposal"]["source"] != "real" or not path:
                raise ProviderUnavailable("M4 action requests are not connected; nothing was executed")
            draft["state"] = "submitting"
            proposal = {**draft["proposal"], "request_id": token}
        # The configured route MUST create a pending request, never execute or approve.
        result = provider.request("POST", path, proposal)
        with self.lock:
            draft["state"] = "submitted"
        return {"status": "request_submitted", "executed": False, "approval_required": True, "m4_response": result}
