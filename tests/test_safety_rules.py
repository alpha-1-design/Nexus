"""Tests for the safety rule semantics added in the 2026-11-19 M6 round:

- ``Rule.blocked_extensions`` now has real semantics (suffix match on the
  target path or any command token) — used by the ``check-gitignore`` rule.
- ``no-hardcoded-secrets`` skips plain environment-variable management
  (``export`` / ``unset`` / ``env `` prefixes) while still blocking secrets
  embedded in other commands (e.g. ``curl -H "Authorization: TOKEN=..."``).
"""

import pytest

from nexus.safety import RuleCategory, RuleLevel, SafetyMode, get_safety_engine


@pytest.fixture(autouse=True)
def _non_strict_mode():
    """Judge the rules themselves (warn stays warn), and restore the engine
    singleton's default strict mode afterwards — conftest only resets the
    safety MODE, not the strict flag."""
    engine = get_safety_engine()
    engine.set_mode(SafetyMode.USER_REVIEW)
    engine.disable_strict_mode()
    yield
    engine.set_mode(SafetyMode.USER_REVIEW)
    engine.enable_strict_mode()


def _check(context: dict):
    """Run the global engine and return (violations_by_rule_id, should_proceed)."""
    engine = get_safety_engine()
    violations = engine.check(context)
    proceed, reason = engine.should_proceed(violations)
    return {v.rule.id for v in violations}, proceed


# ---------------------------------------------------------------------------
# blocked_extensions (check-gitignore rule)
# ---------------------------------------------------------------------------


def test_gitignore_rule_fires_on_env_path_write():
    ids, _ = _check({"tool": "write", "path": "/home/user/project/.env"})
    assert "check-gitignore" in ids


def test_gitignore_rule_fires_on_staging_command_token():
    ids, _ = _check({"tool": "bash", "command": "git add .env README.md"})
    assert "check-gitignore" in ids


def test_gitignore_rule_covers_other_secret_extensions():
    for name in ("server.pem", "id_rsa.key", "cert.p12", "cert.pfx"):
        ids, _ = _check({"tool": "write", "path": f"/home/user/{name}"})
        assert "check-gitignore" in ids, name


def test_gitignore_rule_case_insensitive():
    ids, _ = _check({"tool": "write", "path": "/home/user/ENV_FILE.ENV"})
    assert "check-gitignore" in ids


def test_gitignore_rule_quiet_on_normal_files():
    ids, _ = _check({"tool": "write", "path": "/home/user/project/main.py"})
    assert "check-gitignore" not in ids


def test_gitignore_rule_is_warning_not_block():
    engine = get_safety_engine()
    rule = engine.rules["check-gitignore"]
    assert rule.level == RuleLevel.WARN
    assert rule.category == RuleCategory.SECURITY
    # And a normal write still proceeds (warn only, non-strict mode).
    ids, proceed = _check({"tool": "write", "path": "/home/user/project/.env"})
    assert proceed is True


# ---------------------------------------------------------------------------
# no-hardcoded-secrets: env management allowed, embedded secrets blocked
# ---------------------------------------------------------------------------


def test_export_assignment_is_not_blocked():
    ids, proceed = _check({"tool": "bash", "command": "export OPENAI_API_KEY=sk-verylongvalue123"})
    assert "no-hardcoded-secrets" not in ids
    assert proceed is True


def test_export_multiple_vars_not_blocked():
    ids, proceed = _check({"tool": "bash", "command": "export GROQ_TOKEN=gsk_abcdefgh NEXUS_TOKEN=abcdefgh"})
    assert "no-hardcoded-secrets" not in ids
    assert proceed is True


def test_unset_is_not_blocked():
    ids, proceed = _check({"tool": "bash", "command": "unset OPENAI_API_KEY"})
    assert "no-hardcoded-secrets" not in ids
    assert proceed is True


def test_env_prefix_assignment_not_blocked():
    ids, proceed = _check({"tool": "bash", "command": "env BOT_TOKEN=abcd1234efgh ./run.sh"})
    assert "no-hardcoded-secrets" not in ids
    assert proceed is True


def test_curl_authorization_token_still_blocked():
    ids, proceed = _check({"tool": "bash", "command": 'curl -H "Authorization: TOKEN=abcd1234efgh" https://api.example.com'})
    assert "no-hardcoded-secrets" in ids
    assert proceed is False


def test_plain_secret_assignment_still_blocked():
    ids, proceed = _check({"tool": "bash", "command": "deploy.py --api-key=sk-live-abcdefgh123"})
    assert "no-hardcoded-secrets" in ids
    assert proceed is False


def test_export_allows_short_values_too():
    """Prefix allowlist applies before the pattern — even trivial exports pass."""
    ids, _ = _check({"tool": "bash", "command": "export PS1=customized"})
    assert "no-hardcoded-secrets" not in ids


def test_allowlist_does_not_leak_to_other_rules():
    """The prefix gate is per-rule: destructive rules still see export'd commands."""
    ids, proceed = _check({"tool": "bash", "command": "export PATH=/tmp && rm -rf /data"})
    assert "no-rm-rf" in ids
    assert proceed is False
