"""
Петля обучения. Режим через env FEEDBACK_MODE:

  off          — обучения нет (по умолчанию). generate получает пустой блок.
  admin_choice — учимся на РЕШЕНИЯХ АДМИНА (никаких внешних метрик):
                   что чаще одобряют (особенно без правок) -> образец стиля,
                   что отклоняют/сильно правят -> чего избегать,
                   перекос A/B -> подсказка.
  metrics      — старое: FTD/CTR по subid из Voonix/MailMind (когда настроишь атрибуцию).

Блок инжектится в промпт как geo.learned_winners.
"""
import os

from .store import get_store

MODE = os.environ.get("FEEDBACK_MODE", "off").lower()
MIN_SAMPLES = int(os.environ.get("FEEDBACK_MIN_SAMPLES", "8"))   # для admin_choice
MIN_CLICKS = int(os.environ.get("FEEDBACK_MIN_CLICKS", "20"))    # для metrics
TOP_N = int(os.environ.get("FEEDBACK_TOP_N", "5"))


# ---- helpers --------------------------------------------------------------
def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return (text or "").strip()


def _chosen_hook(d):
    """Хук одобренного черновика: для email — сабжект, для telegram — первая строка."""
    if d.get("hook"):                         # проставляется при доставке
        return d["hook"]
    if d.get("kind") == "email":
        return d.get("edited_subject") or (
            d.get("subject_b") if d.get("chosen_variant") == "B" else d.get("subject_a"))
    text = d.get("edited_text") or (
        d.get("post_b") if d.get("chosen_variant") == "B" else d.get("post_a"))
    return _first_line(text)


def _both_hooks(d):
    """Оба варианта (для отклонённых — негативный сигнал)."""
    if d.get("kind") == "email":
        return [h for h in (d.get("subject_a"), d.get("subject_b")) if h]
    return [_first_line(h) for h in (d.get("post_a"), d.get("post_b")) if h]


# ---- режим admin_choice ---------------------------------------------------
def _admin_choice_block(geo, kind):
    drafts = get_store().list_decided(geo, kind)
    if len(drafts) < MIN_SAMPLES:
        return ""

    approved = [d for d in drafts if d.get("status") in ("approved", "published")]
    rejected = [d for d in drafts if d.get("status") == "rejected"]
    if not approved:
        return ""

    # хуки одобренных; чистые (без правок) — сильнее
    clean = [_chosen_hook(d) for d in approved if not d.get("edited_text") and not d.get("edited_subject")]
    edited = [_chosen_hook(d) for d in approved if d.get("edited_text") or d.get("edited_subject")]
    rej_hooks = [h for d in rejected for h in _both_hooks(d)]

    a = sum(1 for d in approved if d.get("chosen_variant") == "A")
    b = sum(1 for d in approved if d.get("chosen_variant") == "B")

    label = "сабжекты" if kind == "email" else "хуки"
    lines = [f"Сигнал от админа по этому GEO ({len(approved)} одобрено, {len(rejected)} отклонено) — "
             f"ориентируйся на стиль, НЕ копируй дословно:"]
    if clean:
        lines.append(f"Одобряет как есть (лучший образец, {label}):")
        for h in [x for x in clean if x][:TOP_N]:
            lines.append(f"  • «{h}»")
    if rej_hooks:
        lines.append(f"Отклоняет (избегай похожего):")
        for h in rej_hooks[:3]:
            lines.append(f"  • «{h}»")
    if a + b >= 5 and abs(a - b) >= 3:
        pref = "A" if a > b else "B"
        lines.append(f"Чаще одобряет вариант {pref} ({a}×A / {b}×B) — держи этот тип хука первым.")
    return "\n".join(lines) if len(lines) > 1 else ""


# ---- режим metrics (старое; включается позже) -----------------------------
def _metrics_block(geo, kind):
    from .metrics import get_metrics_source
    published = [d for d in get_store().list_published(geo) if not kind or d.get("kind") == kind]
    if not published:
        return ""
    perf = get_metrics_source().get([d["subid"] for d in published])
    by_hook = {}
    for d in published:
        hook = (d.get("hook") or "").strip()
        if not hook:
            continue
        m = perf.get(d["subid"], {"clicks": 0, "ftd": 0})
        agg = by_hook.setdefault(hook, {"hook": hook, "posts": 0, "clicks": 0, "ftd": 0})
        agg["posts"] += 1; agg["clicks"] += int(m.get("clicks", 0)); agg["ftd"] += int(m.get("ftd", 0))
    rows = []
    for agg in by_hook.values():
        agg["ctr"] = round(agg["ftd"] / agg["clicks"], 4) if agg["clicks"] else 0.0
        if agg["clicks"] >= MIN_CLICKS:
            rows.append(agg)
    if not rows:
        return ""
    label = "сабжекты" if kind == "email" else "хуки"
    winners = sorted(rows, key=lambda r: (r["ftd"], r["ctr"]), reverse=True)[:TOP_N]
    lines = ["Исторические данные по этому GEO (учись, НЕ копируй):", f"Лучшие {label} по конверсии:"]
    for r in winners:
        lines.append(f"  • «{r['hook']}» — {r['ftd']} FTD, конв./клик {r['ctr']:.1%}")
    return "\n".join(lines)


# ---- точка входа ----------------------------------------------------------
def get_winners_block(geo, kind=None):
    if MODE == "admin_choice":
        return _admin_choice_block(geo, kind)
    if MODE == "metrics":
        return _metrics_block(geo, kind)
    return ""   # off


def report(geo=None, kind=None):
    print(f"=== Feedback ({MODE}) geo={geo or 'all'} kind={kind or 'all'} ===")
    block = get_winners_block(geo, kind) if MODE != "off" else ""
    print(block or "(пусто: режим off, либо мало данных)")


if __name__ == "__main__":
    import sys
    report(sys.argv[1] if len(sys.argv) > 1 else None,
           sys.argv[2] if len(sys.argv) > 2 else None)
