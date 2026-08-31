"""Pseudonymization of live-account identities.

Attest sweeps an account containing real people and real infrastructure, and its
output is designed to be handed to a third party — an auditor, a prospective
customer, a hackathon judge. Identity handling therefore has to be deliberate.

**Redaction happens at the tool boundary**, before a result is returned to the
agent. The consequence is that the model never sees a real IAM user name or
email at all, so real identities cannot leak into the conversation transcript,
the run timeline rendered in the dashboard, a screenshot, or a demo recording —
not merely into the stored evidence.

Three properties make this usable rather than merely safe:

1. **Deterministic.** The same input maps to the same pseudonym across runs, so
   drift comparison between two sweeps still works ("the same user still lacks
   MFA" is expressible without knowing who they are).
2. **Structure-preserving.** Counts, shapes and relationships survive, so the
   agent can still reason: three distinct users remain three distinct users.
3. **Reversible by the account owner only.** A local, gitignored map lets the
   operator resolve `iam-user-a3f2c1` back to a real name. The map never leaves
   the machine; the pseudonyms are what travel.

Demo resources (`attest-demo-*`) are deliberately NOT pseudonymized: they are
synthetic, they are the subject of the demo, and remediation must address them
by their real names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from tools.config import DEMO_PREFIX, REPO_ROOT, TABLE_PREFIX

# Local, gitignored. Holds the salt and the pseudonym -> real value map.
LOCAL_DIR = REPO_ROOT / ".attest_local"
SALT_FILE = LOCAL_DIR / "redaction_salt"
MAP_FILE = LOCAL_DIR / "redaction_map.json"

ACCOUNT_ID_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ARN_RE = re.compile(r"arn:aws[a-z-]*:[^:]*:[^:]*:(\d{12})?:")

# Keys whose values name a principal or resource. Matched case-insensitively by
# exact key name; anything unmatched still gets free-text scrubbing.
KEY_KINDS: dict[str, str] = {
    "user": "iam-user",
    "username": "iam-user",
    "user_name": "iam-user",
    "email": "email",
    "bucket": "bucket",
    "group_id": "sg",
    "group_name": "sg-name",
    "vpc_id": "vpc",
    "detector_id": "detector",
    "trail_name": "trail",
    "name": "resource",
    "arn": "arn",
    "key_id": "key",
    "access_key_id": "access-key",
    "kms_key": "kms-key",
    "default_kms_key_id": "kms-key",
    # Keys holding a LIST OF BARE NAMES. These need explicit entries: walking a
    # list passes the parent key down to each string, so without them the names
    # fall through to free-text scrubbing and survive intact.
    "unencrypted": "bucket",
    "not_meeting_kms_requirement": "bucket",
    "not_fully_blocked": "bucket",
    "multi_region_logging_trails": "trail",
    "open_groups": "sg-name",
}

# Values that are AWS-owned or structural, never personal. Left in the clear so
# the agent can reason about them.
SAFE_VALUES = {
    "alias/aws/ebs",
    "alias/aws/s3",
    "AES256",
    "aws:kms",
    "<root_account>",
    "default",
}


def _is_exempt(value: str) -> bool:
    """Demo and Attest-owned resources stay readable."""
    return value.startswith(DEMO_PREFIX) or value.startswith(f"{TABLE_PREFIX}-evidence")


class Redactor:
    """Deterministic, salted pseudonymizer with a local reverse map."""

    def __init__(self, salt: str | None = None, persist: bool = True):
        self._salt = salt or _load_or_create_salt()
        self._persist = persist
        self._map: dict[str, str] = {}
        if persist and MAP_FILE.exists():
            try:
                self._map = json.loads(MAP_FILE.read_text())
            except (OSError, json.JSONDecodeError):
                self._map = {}

    # -- core ------------------------------------------------------------
    def pseudonym(self, kind: str, value: str) -> str:
        """Stable pseudonym for `value`, namespaced by `kind`."""
        digest = hashlib.sha256(
            f"{self._salt}|{kind}|{value}".encode("utf-8")
        ).hexdigest()[:8]
        token = f"{kind}-{digest}"
        if self._map.get(token) != value:
            self._map[token] = value
            self._dirty = True
        return token

    def _scrub_account(self, text: str) -> str:
        """Replace bare 12-digit account ids wherever they appear."""
        return ACCOUNT_ID_RE.sub(lambda m: self.pseudonym("account", m.group(1)), text)

    def _scrub_arn(self, text: str) -> str:
        """Scrub an ARN: account id, plus the trailing resource/principal name.

        `arn:aws:iam::123456789012:user/alice` leaks `alice` in the resource
        path, so scrubbing the account id alone is not enough.
        """

        def _one(m: re.Match[str]) -> str:
            arn = m.group(0)
            head, sep, resource = arn.rpartition("/")
            if not sep:
                # `arn:...:resource-type:name` form — split on the last colon.
                head, sep, resource = arn.rpartition(":")
            if not resource or resource in SAFE_VALUES or _is_exempt(resource):
                return self._scrub_account(arn)
            return self._scrub_account(head) + sep + self.pseudonym("resource", resource)

        return re.sub(r"arn:aws[a-z-]*:\S+", _one, text)

    def _scrub_text(self, text: str) -> str:
        """Scrub identifiers embedded in free text (messages, errors, ARNs)."""
        text = self._scrub_arn(text)
        text = self._scrub_account(text)
        text = EMAIL_RE.sub(lambda m: self.pseudonym("email", m.group(0)), text)
        return text

    def value(self, key: str | None, value: Any) -> Any:
        """Redact one value, using `key` to decide how aggressively."""
        if not isinstance(value, str):
            return value
        if value in SAFE_VALUES:
            return value
        # Demo/Attest resources keep their readable names, but an account id
        # embedded in them (bucket names are account-suffixed) is still scrubbed.
        if _is_exempt(value):
            return self._scrub_account(value)

        kind = KEY_KINDS.get((key or "").lower())

        if kind == "arn" or value.startswith("arn:aws"):
            return self._scrub_text(value)
        if kind == "email" or EMAIL_RE.fullmatch(value):
            return self.pseudonym("email", value)
        if kind:
            # A named principal or resource: replace wholesale.
            return self.pseudonym(kind, value)
        return self._scrub_text(value)

    def _walk1(self, obj: Any, key: str | None = None) -> Any:
        """Structure-aware pass: redact by key, preserving shape exactly."""
        if isinstance(obj, dict):
            return {k: self._walk1(v, key=k) for k, v in obj.items()}
        if isinstance(obj, list):
            # A list inherits its parent's key, so `{"unencrypted": ["a","b"]}`
            # redacts each name as a bucket.
            return [self._walk1(v, key=key) for v in obj]
        return self.value(key, obj)

    def _sweep(self, obj: Any) -> Any:
        """Defensive second pass.

        Replaces any value we have already pseudonymized wherever it still
        appears verbatim — catching names that reached a key the structure-aware
        pass does not recognise. Without this, adding a tool that returns a new
        list-of-names key would silently leak until someone noticed.
        """
        pairs = [
            (real, token)
            for token, real in self._map.items()
            if len(real) >= 4 and not _is_exempt(real) and real not in SAFE_VALUES
        ]
        if not pairs:
            return obj
        # Longest first, so a name that contains another is replaced whole.
        pairs.sort(key=lambda p: len(p[0]), reverse=True)

        def fix(o: Any) -> Any:
            if isinstance(o, dict):
                return {k: fix(v) for k, v in o.items()}
            if isinstance(o, list):
                return [fix(v) for v in o]
            if isinstance(o, str):
                for real, token in pairs:
                    if real in o:
                        o = o.replace(real, token)
                return o
            return o

        return fix(obj)

    def walk(self, obj: Any, key: str | None = None) -> Any:
        """Recursively redact a tool result, preserving structure exactly."""
        return self._sweep(self._walk1(obj, key=key))

    # -- persistence -----------------------------------------------------
    def save(self) -> None:
        """Write the reverse map locally. Never leaves the machine."""
        if not self._persist:
            return
        LOCAL_DIR.mkdir(exist_ok=True)
        MAP_FILE.write_text(json.dumps(self._map, indent=2, sort_keys=True))
        os.chmod(MAP_FILE, 0o600)

    def resolve(self, token: str) -> str | None:
        """Owner-side lookup: pseudonym -> real value."""
        return self._map.get(token)

    def unredact(self, text: str) -> str:
        """Turn a pseudonymized string back into the real one.

        The inverse of `_sweep`, and the reason the agent can operate entirely in
        pseudonyms. The model sees `attest-demo-logs-account-f685301e`; AWS only
        knows `attest-demo-logs-<account-id>`. Without this, an approval would be
        bound to a resource that does not exist and remediation would fail with
        NoSuchBucket.

        Applied at the point a name is about to be used against AWS, never to
        anything the model reads back.
        """
        if not isinstance(text, str) or not text:
            return text
        # Longest tokens first so a token containing another is replaced whole.
        for token in sorted(self._map, key=len, reverse=True):
            if token in text:
                text = text.replace(token, self._map[token])
        return text


def _load_or_create_salt() -> str:
    """Persist a salt so pseudonyms are stable across runs (drift comparison)."""
    env = os.environ.get("ATTEST_REDACTION_SALT")
    if env:
        return env
    if SALT_FILE.exists():
        return SALT_FILE.read_text().strip()
    LOCAL_DIR.mkdir(exist_ok=True)
    salt = secrets.token_hex(16)
    SALT_FILE.write_text(salt)
    os.chmod(SALT_FILE, 0o600)
    return salt


_default: Redactor | None = None


def default_redactor() -> Redactor:
    global _default
    if _default is None:
        _default = Redactor()
    return _default


def redact(obj: Any) -> Any:
    """Redact a structure with the process-wide redactor and persist the map."""
    r = default_redactor()
    out = r.walk(obj)
    r.save()
    return out


def redaction_enabled() -> bool:
    """Off only for an explicitly-declared scratch account.

    Defaults to ON: the failure mode of redacting a throwaway account is a
    slightly less readable demo, while the failure mode of not redacting a real
    one is publishing colleagues' identities.
    """
    return os.environ.get("ATTEST_REDACT", "1").lower() not in ("0", "false", "no")


def unredact(text: str) -> str:
    """Resolve a pseudonymized identifier back to the real one.

    Call this at the boundary where a name is used against AWS. Never call it on
    anything that is returned to the model.
    """
    if not redaction_enabled():
        return text
    return default_redactor().unredact(text)
