"""
Петля обратной связи (моат).

published посты (несут subid + hook) × метрики по subid (clicks/FTD)
  -> агрегируем по хукам внутри GEO
  -> формируем «winners block»: что историч. гонит FTD в этом гео
  -> блок инжектится в промпт генерации (geo.learned_winners).

Чем больше гео и объёма -> больше данных -> точнее пишет. Масштаб усиливает качество.
"""
import os

from .store import get_store
from .metrics import get_metrics_source

MIN_CLICKS = int(os.environ.get("FEEDBACK_MIN_CLICKS", "20"))   # порог значимости
TOP_N = int(os.environ.get("FEEDBACK_TOP_N", "5"))


def _aggregate(geo):
    """Вернуть список {hook, posts, clicks, ftd, ctr, ftd_rate} по хукам гео."""
    store = get_store()
    published = store.list_published(geo)
    if not published:
        return []

    subids = [d["subid"] for d in published]
    perf = get_metrics_source().get(subids)

    by_hook = {}
    for d in published:
        hook = (d.get("hook") or "").strip()
        if not hook:
            continue
        m = perf.get(d["subid"], {"clicks": 0, "ftd": 0})
        agg = by_hook.setdefault(hook, {"hook": hook, "posts": 0, "clicks": 0, "ftd": 0})
        agg["posts"] += 1
        agg["clicks"] += int(m.get("clicks", 0))
        agg["ftd"] += int(m.get("ftd", 0))

    rows = []
    for agg in by_hook.values():
        clicks = agg["clicks"]
        agg["ctr"] = round(agg["ftd"] / clicks, 4) if clicks else 0.0   # FTD / click
        rows.append(agg)
    return rows


def get_winners_block(geo):
    """Текстовый блок для промпта. Пусто, если данных мало (холодный старт)."""
    rows = [r for r in _aggregate(geo) if r["clicks"] >= MIN_CLICKS]
    if not rows:
        return ""

    winners = sorted(rows, key=lambda r: (r["ftd"], r["ctr"]), reverse=True)[:TOP_N]
    losers = sorted(rows, key=lambda r: (r["ctr"], r["ftd"]))[:2]

    lines = ["Исторические данные по этому GEO (учись на них, НЕ копируй дословно):",
             "Лучшие хуки по FTD:"]
    for r in winners:
        lines.append(f"  • «{r['hook']}» — {r['ftd']} FTD, FTD/клик {r['ctr']:.1%} (постов: {r['posts']})")
    if losers and losers[0]["ctr"] == 0:
        lines.append("Слабые углы (избегай похожего):")
        for r in losers:
            lines.append(f"  • «{r['hook']}» — {r['ftd']} FTD при {r['clicks']} кликах")
    return "\n".join(lines)


def report(geo=None):
    """CLI-отчёт по перформансу хуков."""
    rows = sorted(_aggregate(geo), key=lambda r: r["ftd"], reverse=True)
    print(f"=== Feedback report (geo={geo or 'all'}) ===")
    if not rows:
        print("нет опубликованных постов с метриками.")
        return
    for r in rows:
        print(f"FTD={r['ftd']:>3}  FTD/clk={r['ctr']:.1%}  posts={r['posts']:>2}  | {r['hook'][:70]}")


if __name__ == "__main__":
    import sys
    report(sys.argv[1] if len(sys.argv) > 1 else None)
