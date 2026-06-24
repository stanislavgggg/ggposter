"""Загрузка конфигов Слоя 4 (geo + sport) с подстановкой ${ENV} переменных."""
import os
import re
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value):
    """Рекурсивно подставляет ${VAR} из окружения в строки конфига."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return _expand(yaml.safe_load(f))


def load_geo(geo=None):
    geo = geo or os.environ.get("PILOT_GEO", "lt")
    return load_yaml(os.path.join(ROOT, "config", "geo", f"{geo}.yaml"))


def load_sport(sport=None):
    sport = sport or os.environ.get("PILOT_SPORT", "football")
    return load_yaml(os.path.join(ROOT, "config", "sport", f"{sport}.yaml"))


def load_prompt(name="post_generation"):
    with open(os.path.join(ROOT, "prompts", f"{name}.md"), "r", encoding="utf-8") as f:
        return f.read()
