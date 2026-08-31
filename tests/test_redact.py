"""Tests for the pseudonymization boundary.

This layer is what keeps real colleagues' identities out of stored evidence,
trust packets, screenshots and demo recordings, so it is tested for leaks
directly rather than only for happy-path shape.
"""

from __future__ import annotations

import re

import pytest

from tools.redact import ACCOUNT_ID_RE, EMAIL_RE, Redactor

ACCOUNT = "123456789012"


@pytest.fixture
def r() -> Redactor:
    # persist=False keeps tests off the local map file.
    return Redactor(salt="test-salt", persist=False)


# -- determinism -------------------------------------------------------------


def test_same_input_gives_same_pseudonym(r):
    assert r.value("user", "alice") == r.value("user", "alice")


def test_different_inputs_give_different_pseudonyms(r):
    assert r.value("user", "alice") != r.value("user", "bob")


def test_pseudonyms_are_salt_dependent():
    a = Redactor(salt="salt-a", persist=False).value("user", "alice")
    b = Redactor(salt="salt-b", persist=False).value("user", "alice")
    assert a != b


def test_kind_namespacing_separates_identical_values(r):
    """The same string as a user and as a bucket must not collide."""
    assert r.value("user", "shared-name") != r.value("bucket", "shared-name")


# -- leak prevention ---------------------------------------------------------


def test_email_is_pseudonymized_by_key(r):
    assert r.value("email", "person@example.com") == r.value("email", "person@example.com")
    assert "@" not in r.value("email", "person@example.com")


def test_email_is_caught_even_under_an_unrelated_key(r):
    """A bare email must not survive because its key was not in KEY_KINDS."""
    out = r.value("some_unmapped_field", "person@example.com")
    assert "@" not in out


def test_account_id_scrubbed_from_free_text(r):
    out = r.value("message", f"Access denied for account {ACCOUNT}")
    assert ACCOUNT not in out


def test_arn_scrubs_both_account_and_principal(r):
    out = r.value("arn", f"arn:aws:iam::{ACCOUNT}:user/alice")
    assert ACCOUNT not in out
    assert "alice" not in out


def test_arn_detected_without_a_matching_key(r):
    out = r.value("unmapped", f"arn:aws:iam::{ACCOUNT}:user/alice")
    assert ACCOUNT not in out and "alice" not in out


def test_exempt_demo_resource_keeps_name_but_loses_account_id(r):
    """Demo resources stay readable; the account id suffix still goes."""
    out = r.value("bucket", f"attest-demo-logs-{ACCOUNT}")
    assert out.startswith("attest-demo-logs-")
    assert ACCOUNT not in out


def test_no_account_id_or_email_survives_a_realistic_payload(r):
    payload = {
        "buckets": [
            {"bucket": f"prod-data-{ACCOUNT}", "encrypted": False},
            {"bucket": f"attest-demo-logs-{ACCOUNT}", "encrypted": False},
        ],
        "users_without_mfa": [
            {"user": "real.person", "email": "real.person@example.com"}
        ],
        "message": f"denied for arn:aws:iam::{ACCOUNT}:user/real.person",
        "nested": {"deep": [{"arn": f"arn:aws:s3:::bucket-{ACCOUNT}"}]},
    }
    blob = repr(r.walk(payload))
    assert ACCOUNT not in blob
    assert not EMAIL_RE.search(blob)
    assert "real.person" not in blob
    assert not ACCOUNT_ID_RE.search(blob)


# -- structure preservation --------------------------------------------------


def test_structure_and_types_are_preserved(r):
    payload = {
        "count": 3,
        "compliant": False,
        "ratio": 0.5,
        "missing": None,
        "buckets": [{"bucket": "a", "encrypted": True}, {"bucket": "b", "encrypted": False}],
    }
    out = r.walk(payload)
    assert out["count"] == 3
    assert out["compliant"] is False
    assert out["ratio"] == 0.5
    assert out["missing"] is None
    assert len(out["buckets"]) == 2
    assert out["buckets"][0]["encrypted"] is True


def test_distinct_users_stay_distinct(r):
    """Cardinality must survive, or the agent miscounts findings."""
    users = [{"user": u} for u in ("a", "b", "c")]
    out = r.walk(users)
    assert len({u["user"] for u in out}) == 3


def test_safe_values_pass_through(r):
    assert r.value("default_kms_key_id", "alias/aws/ebs") == "alias/aws/ebs"
    assert r.value("algorithm", "AES256") == "AES256"
    assert r.value("user", "<root_account>") == "<root_account>"


def test_non_strings_untouched(r):
    assert r.value("count", 12) == 12
    assert r.value("flag", True) is True
    assert r.value("nothing", None) is None


# -- reversibility -----------------------------------------------------------


def test_owner_can_resolve_a_pseudonym_back(r):
    token = r.value("user", "alice")
    assert r.resolve(token) == "alice"


def test_resolve_returns_none_for_unknown_token(r):
    assert r.resolve("iam-user-deadbeef") is None


# -- drift comparison --------------------------------------------------------


def test_pseudonyms_are_stable_across_redactor_instances():
    """Two runs must agree, or run-over-run drift comparison breaks."""
    a = Redactor(salt="fixed", persist=False).value("user", "alice")
    b = Redactor(salt="fixed", persist=False).value("user", "alice")
    assert a == b


def test_pseudonym_format_is_readable(r):
    """Pseudonyms should read as identifiers in a report, not as raw hashes."""
    assert re.fullmatch(r"iam-user-[0-9a-f]{8}", r.value("user", "alice"))
