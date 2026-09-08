"""Configuration loading from YAML and environment variables."""

import os
import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path.home() / ".konstruktor" / "config.yaml"

ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${VAR} patterns in config values."""
    if isinstance(value, str):
        def replacer(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return ENV_VAR_PATTERN.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge two dictionaries. Override takes precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str | Path | None = None) -> dict:
    """Load configuration, merging defaults, user config, and environment."""
    load_dotenv()

    # 1. Load defaults from package data so installed wheels do not depend on
    # the source tree layout.
    default_config = resources.files("konstruktor").joinpath("config.default.yaml")
    config = yaml.safe_load(default_config.read_text(encoding="utf-8")) or {}

    # 2. Merge user config if exists
    user_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if user_path.exists():
        with open(user_path) as f:
            user_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_config)

    # 3. Apply environment variable overrides
    env_overrides = {
        "llm": {},
        "limits": {},
        "logging": {},
    }

    llm_env = {
        "api_key": "KONSTRUKTOR_LLM_API_KEY",
        "model": "KONSTRUKTOR_LLM_MODEL",
        "base_url": "KONSTRUKTOR_LLM_BASE_URL",
    }
    legacy_llm_env = {
        "api_key": "LLM_API_KEY",
        "model": "LLM_MODEL",
        "base_url": "LLM_BASE_URL",
    }
    for key, name in legacy_llm_env.items():
        if os.environ.get(name):
            env_overrides["llm"][key] = os.environ[name]
    for key, name in llm_env.items():
        if os.environ.get(name):
            env_overrides["llm"][key] = os.environ[name]
    if os.environ.get("KONSTRUKTOR_TIMEOUT"):
        env_overrides["limits"]["timeout"] = int(os.environ["KONSTRUKTOR_TIMEOUT"])
    if os.environ.get("KONSTRUKTOR_LOG_DIR"):
        env_overrides["logging"]["dir"] = os.environ["KONSTRUKTOR_LOG_DIR"]

    config = _deep_merge(config, env_overrides)

    # 4. Resolve ${VAR} patterns
    config = _resolve_env_vars(config)

    return config


def validate_config(config: dict) -> list[str]:
    """Validate configuration and return a list of error messages."""
    errors = []

    if not config.get("llm", {}).get("api_key"):
        errors.append(
            "API key is not set. Set LLM_API_KEY environment variable "
            "or llm.api_key in ~/.konstruktor/config.yaml"
        )

    mode = config.get("agent", {}).get("mode", "auto")
    if mode not in ("auto", "ask", "llm"):
        errors.append(f"Invalid agent.mode: {mode!r}. Must be 'auto', 'ask', or 'llm'.")

    sandbox = config.get("agent", {}).get("sandbox", "docker")
    if sandbox not in ("docker", "process", "remote"):
        errors.append(f"Invalid agent.sandbox: {sandbox!r}.")

    timeout = config.get("limits", {}).get("timeout", 0)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        errors.append(f"limits.timeout must be positive, got {timeout!r}.")

    step_timeout = config.get("limits", {}).get("step_timeout")
    if not isinstance(step_timeout, (int, float)) or step_timeout <= 0:
        errors.append(f"limits.step_timeout must be positive, got {step_timeout!r}.")

    max_steps = config.get("limits", {}).get("max_steps")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
        errors.append(f"limits.max_steps must be a positive integer, got {max_steps!r}.")

    retries = config.get("limits", {}).get("max_retries", 0)
    if not isinstance(retries, int) or retries < 0:
        errors.append(f"limits.max_retries must be a non-negative integer, got {retries!r}.")

    return errors
