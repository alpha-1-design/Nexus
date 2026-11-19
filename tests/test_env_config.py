"""Tests for the global .env machinery in nexus.config (stdlib-only parser)."""

import json
import os
import stat
from pathlib import Path

import pytest

from nexus import config as nexus_config
from nexus.config import (
    NexusConfig,
    ProviderConfig,
    load_config,
    load_env,
    parse_env_text,
    save_config,
    write_env_key,
)


class TestParseEnvText:
    def test_simple_assignment(self):
        parsed, bad = parse_env_text("KEY=value\n")
        assert parsed == {"KEY": "value"}
        assert bad == 0

    def test_export_prefix(self):
        parsed, bad = parse_env_text("export KEY=value\nexport OTHER=2\n")
        assert parsed == {"KEY": "value", "OTHER": "2"}
        assert bad == 0

    def test_double_quotes(self):
        parsed, _ = parse_env_text('KEY="hello world"\n')
        assert parsed == {"KEY": "hello world"}

    def test_double_quote_escapes(self):
        parsed, _ = parse_env_text('KEY="a \\"quoted\\" value"\n')
        assert parsed == {"KEY": 'a "quoted" value'}

    def test_single_quotes_are_literal(self):
        parsed, _ = parse_env_text("KEY='a \"quoted\" value'\n")
        assert parsed == {"KEY": 'a "quoted" value'}

    def test_comments_and_blank_lines(self):
        text = "# comment\n\n   \n# another\nKEY=value\n"
        parsed, bad = parse_env_text(text)
        assert parsed == {"KEY": "value"}
        assert bad == 0

    def test_trailing_comment_on_unquoted_value(self):
        parsed, _ = parse_env_text("KEY=value # trailing comment\n")
        assert parsed == {"KEY": "value"}

    def test_whitespace_tolerance(self):
        parsed, _ = parse_env_text("  KEY  =  spaced value  \n")
        assert parsed == {"KEY": "spaced value"}

    def test_malformed_lines_skipped_and_counted(self):
        parsed, bad = parse_env_text("NO_EQUALS_SIGN\n=NOKEY\nGOOD=1\n123BAD=2\n")
        assert parsed == {"GOOD": "1"}
        assert bad == 3

    def test_dot_in_key_is_malformed(self):
        parsed, bad = parse_env_text("BAD.KEY=1\nGOOD=2\n")
        assert parsed == {"GOOD": "2"}
        assert bad == 1


class TestLoadEnv:
    def _write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_home_wins_over_cwd(self, tmp_path):
        home_env = self._write(tmp_path / "home" / ".env", "SHARED=from_home\nHOME_ONLY=1\n")
        cwd_env = self._write(tmp_path / "cwd" / ".env", "SHARED=from_cwd\nCWD_ONLY=2\n")
        merged = load_env(env_file=home_env, cwd_file=cwd_env, environ={}, create_example=False)
        assert merged["SHARED"] == "from_home"  # earlier file wins
        assert merged["HOME_ONLY"] == "1"
        assert merged["CWD_ONLY"] == "2"

    def test_real_env_not_overridden(self, tmp_path):
        env_file = self._write(tmp_path / ".env", "NEXUS_SECRET=from_file\n")
        environ = {"NEXUS_SECRET": "from_real_env"}
        merged = load_env(
            env_file=env_file,
            cwd_file=tmp_path / "missing.env",
            environ=environ,
            create_example=False,
        )
        assert environ["NEXUS_SECRET"] == "from_real_env"
        assert "NEXUS_SECRET" not in merged

    def test_applies_to_os_environ(self, tmp_path, monkeypatch):
        env_file = self._write(tmp_path / ".env", "NEXUS_TEST_VAR=hello\n")
        monkeypatch.delenv("NEXUS_TEST_VAR", raising=False)
        load_env(
            env_file=env_file,
            cwd_file=tmp_path / "missing.env",
            environ=os.environ,
            create_example=False,
        )
        assert os.environ["NEXUS_TEST_VAR"] == "hello"

    def test_env_example_created_when_env_missing(self, tmp_path):
        home_dir = tmp_path / "nexushome"
        example = home_dir / ".env.example"
        load_env(
            env_file=home_dir / ".env",
            cwd_file=tmp_path / "also-missing.env",
            environ={},
            create_example=True,
        )
        assert example.exists()
        text = example.read_text()
        # All supported provider env keys are present, commented out.
        for key in (
            "GROQ_API_KEY",
            "OPENROUTER_API_KEY",
            "CEREBRAS_API_KEY",
            "GEMINI_API_KEY",
            "MISTRAL_API_KEY",
            "GITHUB_TOKEN",
            "HF_TOKEN",
            "CLOUDFLARE_API_TOKEN",
            "CLOUDFLARE_ACCOUNT_ID",
            "ZHIPUAI_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "TAVILY_API_KEY",
            "BRAVE_API_KEY",
            "EXA_API_KEY",
        ):
            assert f"# {key}=" in text
        # Free tier section comes before the paid providers section.
        assert text.index("FREE TIER") < text.index("PAID PROVIDERS")
        # A real .env is never auto-created.
        assert not (home_dir / ".env").exists()

    def test_chmod_600(self, tmp_path):
        env_file = self._write(tmp_path / ".env", "A=1\n")
        load_env(env_file=env_file, cwd_file=tmp_path / "m.env", environ={}, create_example=False)
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    def test_write_env_key_creates_replaces(self, tmp_path):
        env_file = tmp_path / ".env"
        write_env_key("GROQ_API_KEY", "gsk_aaa", env_path=env_file)
        write_env_key("GROQ_API_KEY", "gsk_bbb", env_path=env_file)
        write_env_key("OPENROUTER_API_KEY", "sk_or", env_path=env_file)
        text = env_file.read_text()
        assert "gsk_aaa" not in text  # replaced, not duplicated
        assert text.count("GROQ_API_KEY=") == 1
        assert "GROQ_API_KEY=gsk_bbb" in text
        assert "OPENROUTER_API_KEY=sk_or" in text
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def _isolate_global_env(tmp_path: Path, monkeypatch) -> None:
    """Point module-level .env paths at tmp so tests never touch real ~/.nexus."""
    monkeypatch.setattr(nexus_config, "DEFAULT_ENV_FILE", tmp_path / "nexushome" / ".env")
    monkeypatch.setattr(nexus_config, "DEFAULT_ENV_EXAMPLE_FILE", tmp_path / "nexushome" / ".env.example")
    monkeypatch.chdir(tmp_path)  # no CWD .env here


class TestConfigKeyFlow:
    def test_env_keys_not_persisted_into_config_json(self, tmp_path, monkeypatch):
        _isolate_global_env(tmp_path, monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        config_path = tmp_path / "config.json"

        cfg = load_config(config_path)

        assert cfg.providers["openai"].api_key == "sk-test-123"  # usable in memory
        data = json.loads(config_path.read_text())
        assert data["providers"]["openai"]["api_key"] is None  # never persisted

    def test_legacy_config_json_keys_still_read(self, tmp_path, monkeypatch):
        _isolate_global_env(tmp_path, monkeypatch)
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "openai": {"name": "openai", "provider_type": "openai", "api_key": "sk-legacy", "model": "gpt-4o"}
                    },
                    "active_provider": "openai",
                }
            )
        )

        cfg = load_config(config_path)

        assert cfg.providers["openai"].api_key == "sk-legacy"
        cfg2 = NexusConfig.from_dict(json.loads(config_path.read_text()))
        assert cfg2.providers["openai"].api_key == "sk-legacy"  # legacy keys round-trip

    def test_env_provider_added_to_existing_config(self, tmp_path, monkeypatch):
        home_env = tmp_path / "nexushome" / ".env"
        home_env.parent.mkdir(parents=True)
        home_env.write_text("GROQ_API_KEY=gsk_new\n", encoding="utf-8")
        monkeypatch.setattr(nexus_config, "DEFAULT_ENV_FILE", home_env)
        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "providers": {"openai": {"name": "openai", "provider_type": "openai", "api_key": "sk-legacy", "model": "gpt-4o"}},
                    "active_provider": "openai",
                }
            )
        )

        cfg = load_config(config_path)

        assert cfg.providers["groq"].api_key == "gsk_new"
        assert cfg.providers["groq"].env_sourced is True
        assert cfg.providers["openai"].api_key == "sk-legacy"  # untouched
        data = json.loads(config_path.read_text())
        assert "groq" not in data["providers"]  # env-sourced key not written back

    def test_active_provider_follows_env_keys(self, tmp_path, monkeypatch):
        _isolate_global_env(tmp_path, monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)

        cfg = load_config(tmp_path / "config.json")

        assert cfg.active_provider == "groq"

    def test_search_key_from_env_not_persisted(self, tmp_path, monkeypatch):
        _isolate_global_env(tmp_path, monkeypatch)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        env_file = tmp_path / "nexushome" / ".env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("TAVILY_API_KEY=tvly-123\n", encoding="utf-8")
        monkeypatch.setattr(nexus_config, "DEFAULT_ENV_FILE", env_file)

        cfg = load_config(tmp_path / "config.json")

        assert cfg.tavily_api_key == "tvly-123"
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["tavily_api_key"] is None

    def test_to_dict_env_sourced_key_is_null(self):
        env_sourced = ProviderConfig(name="groq", provider_type="groq", api_key="gsk_x", env_sourced=True)
        legacy = ProviderConfig(name="groq", provider_type="groq", api_key="gsk_x")
        assert env_sourced.to_dict()["api_key"] is None
        assert legacy.to_dict()["api_key"] == "gsk_x"

    def test_from_dict_tolerates_unknown_fields(self):
        cfg = NexusConfig.from_dict(
            {"providers": {"groq": {"name": "groq", "provider_type": "groq", "future_field": 1}}}
        )
        assert cfg.providers["groq"].provider_type == "groq"

    def test_dashboard_auth_defaults_to_token_and_round_trips(self, tmp_path, monkeypatch):
        _isolate_global_env(tmp_path, monkeypatch)
        config_path = tmp_path / "config.json"

        cfg = load_config(config_path)
        assert cfg.dashboard_auth == "token"  # default

        cfg.dashboard_auth = "strict"
        save_config(cfg, config_path)

        reloaded = load_config(config_path)
        assert reloaded.dashboard_auth == "strict"
        data = json.loads(config_path.read_text())
        assert data["dashboard_auth"] == "strict"  # persisted for dashboard/auth.py

    def test_dashboard_auth_hand_edited_json_is_read(self, tmp_path, monkeypatch):
        """dashboard/auth.py used to need a raw-JSON fallback for this key;
        now the dataclass models it, and a hand-edited config.json still wins."""
        _isolate_global_env(tmp_path, monkeypatch)
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"dashboard_auth": "strict"}))

        cfg = load_config(config_path)
        assert cfg.dashboard_auth == "strict"

    def test_load_config_resolves_home_at_call_time(self, tmp_path, monkeypatch):
        """dashboard/auth.py resolves ~/.nexus at CALL time; load_config must
        agree. Its import-time DEFAULT_CONFIG_FILE snapshot went stale under
        HOME isolation, which made the modeled dashboard_auth field shadow the
        raw config.json fallback (strict-mode-via-config-file broke)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        nexus_home = tmp_path / ".nexus"
        nexus_home.mkdir()
        (nexus_home / "config.json").write_text(json.dumps({"dashboard_auth": "strict"}))

        cfg = load_config()  # no explicit path → resolve from (patched) HOME

        assert cfg.dashboard_auth == "strict"
