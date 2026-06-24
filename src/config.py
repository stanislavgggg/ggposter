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


def load_news():
    return load_yaml(os.path.join(ROOT, "config", "news.yaml"))


def load_prompt(name="post_generation"):
    with open(os.path.join(ROOT, "prompts", f"{name}.md"), "r", encoding="utf-8") as f:
        return f.read()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value):
    s = _SLUG_RE.sub("_", str(value or "").lower()).strip("_")
    return s or "ch"


def telegram_destinations(geo):
    """Назначения Telegram для гео -> [{"id","label","slug"}, ...].

    Поддерживает оба формата config/geo/<geo>.yaml:
      НОВЫЙ:    telegram.destinations: [{id, label}, ...]   (несколько каналов/пабликов)
      ЛЕГАСИ:   telegram.public_channel_id: "..."           (один канал)

    Пустые id и невыставленные ${ENV}-плейсхолдеры отбрасываются (не постим в литерал).
    """
    tg = (geo or {}).get("telegram", {}) or {}
    out = []

    def _valid(cid):
        cid = str(cid or "").strip()
        return cid and "${" not in cid

    raw = tg.get("destinations")
    if raw:
        for d in raw:
            if isinstance(d, dict):
                cid, label = d.get("id"), d.get("label")
                slug = d.get("slug") or cid       # slug из id канала -> уникален и стабилен
            else:
                cid, label, slug = d, d, d
            if _valid(cid):
                out.append({"id": str(cid), "label": str(label or cid), "slug": _slug(slug)})
    else:
        cid = tg.get("public_channel_id")
        if _valid(cid):
            out.append({"id": str(cid), "label": str(cid), "slug": _slug(cid)})
    return out
