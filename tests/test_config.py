"""Tests for the configuration module."""

import yaml

from konstruktor.config import (
    _deep_merge,
    _resolve_env_vars,
    load_config,
    validate_config,
)


class TestDeepMerge:
    def test_override_scalar(self):
        base = {"a": 1, "b": 2}
        override = {"a": 10}
        result = _deep_merge(base, override)
        assert result == {"a": 10, "b": 2}

    def test_nested_dicts(self):
        base = {"llm": {"model": "a", "key": "x"}}
        override = {"llm": {"model": "b"}}
        result = _deep_merge(base, override)
        assert result == {"llm": {"model": "b", "key": "x"}}

    def test_new_key(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}


class TestResolveEnvVars:
    def test_resolve_simple(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        config = {"api_key": "${MY_KEY}"}
        result = _resolve_env_vars(config)
        assert result == {"api_key": "secret123"}

    def test_resolve_missing(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT", raising=False)
        config = {"api_key": "${NONEXISTENT}"}
        result = _resolve_env_vars(config)
        assert result == {"api_key": ""}

    def test_resolve_nested(self, monkeypatch):
        monkeypatch.setenv("KEY", "val")
        config = {"llm": {"api_key": "${KEY}"}}
        result = _resolve_env_vars(config)
        assert result == {"llm": {"api_key": "val"}}


class TestValidateConfig:
    def test_valid_minimal(self):
        cfg = {
            "llm": {"api_key": "sk-123"},
            "agent": {"mode": "auto"},
            "limits": {"timeout": 100, "step_timeout": 20, "max_steps": 10},
        }
        assert validate_config(cfg) == []

    def test_requires_step_limits(self):
        cfg = {
            "llm": {"api_key": "sk-123"},
            "agent": {"mode": "auto"},
            "limits": {"timeout": 100},
        }
        errors = validate_config(cfg)
        assert any("step_timeout" in error for error in errors)
        assert any("max_steps" in error for error in errors)

    def test_missing_api_key(self):
        cfg = {"llm": {}, "agent": {"mode": "auto"}, "limits": {"timeout": 100}}
        errors = validate_config(cfg)
        assert len(errors) >= 1
        assert any("API key" in e for e in errors)

    def test_invalid_mode(self):
        cfg = {"llm": {"api_key": "x"}, "agent": {"mode": "bad"}, "limits": {"timeout": 100}}
        errors = validate_config(cfg)
        assert any("bad" in e for e in errors)

    def test_negative_timeout(self):
        cfg = {"llm": {"api_key": "x"}, "agent": {"mode": "auto"}, "limits": {"timeout": -1}}
        errors = validate_config(cfg)
        assert any("timeout" in e.lower() for e in errors)


class TestLoadConfig:
    def test_shipped_defaults_do_not_enable_unenforceable_command_rules(self, tmp_path):
        result = load_config(tmp_path / "missing.yaml")
        assert result["security"]["allowed_commands"] == []
        assert result["security"]["blocked_commands"] == []

    def test_load_with_env_override(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("LLM_MODEL", "gpt-5.5")

        # Write a user config
        user_config = tmp_path / "config.yaml"
        user_config.write_text(yaml.dump({
            "llm": {"api_key": "sk-test"},
            "limits": {"timeout": 500},
        }))

        result = load_config(user_config)

        assert result["llm"]["model"] == "gpt-5.5"
        assert result["limits"]["timeout"] == 500

    def test_documented_llm_env_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "legacy")
        monkeypatch.setenv("KONSTRUKTOR_LLM_MODEL", "documented")
        monkeypatch.setenv("KONSTRUKTOR_LLM_API_KEY", "secret")
        result = load_config()
        assert result["llm"]["model"] == "documented"
        assert result["llm"]["api_key"] == "secret"
