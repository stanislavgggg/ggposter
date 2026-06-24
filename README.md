# Telegram-автопостинг по гео (iGaming) — конвейер

Контент-менеджер перестаёт быть **автором** и становится **редактором-аппрувером**.
Пилот: **футбол (ЧМ-2026) → Литва (LT) → Telegram**. Масштаб = копирование конфига.

```
Слой 1: событие (Apify→стор)  ─┐
Слой 2: Claude генерит пост    ─┤→ очередь аппрува → менеджер ✅ → Слой 3: автопост в канал
Слой 4: GEO/Sport-конфиг       ─┘                                         │
                                                                          ▼
              петля обучения: метрики по subid (FTD/CTR) → лучшие хуки обратно в промпт
```

## Структура

```
config/geo/{lt,lv,es,hr,pl}.yaml   # Слой 4: язык, тон, оффер, КОМПЛАЕНС (правится без кода)
config/sport/football.yaml         # Слой 4: РЕАЛЬНЫЕ Apify-акторы + field_map, стат-поля
prompts/post_generation.md         # Слой 2: транскреация + рубрика + плейсхолдеры + self-check
schema/bigquery.sql                # прод-стор: match_events + post_drafts (+ метрики)
data/sample_match.json             # фикстура для прогона без Apify
data/metrics.json                  # тестовые метрики для петли обучения
src/
  ingest.py     # Слой 1: Apify(field_map)/sample -> нормализация -> стор
  generate.py   # Слой 2: событие (+выученные хуки) -> Claude -> черновик (идемпотентно, мульти-гео)
  review.py     # вход ревью: REVIEW_SURFACE=telegram|airtable
  bot.py        # ревью в Telegram (always-on): кнопки A/B/Reject + правка реплаем
  airtable_bridge.py # ревью в Airtable: зеркалит драфты в грид, по аппруву публикует
  publish.py    # Слой 3: подстановка {{CTA_LINK}}(+subid)/{{PROMO_CODE}} -> канал
  orchestrate.py# воркер (cron): ingest + generate по всем GEO
  feedback.py   # петля обучения: published × метрики -> winners block для промпта
  metrics.py    # источник перформанса по subid: local | bigquery (твой Voonix/FTD)
  store.py      # стор (единый источник правды): local (тест) | bigquery (прод)
```

## Поверхность ревью: Telegram или Airtable

`REVIEW_SURFACE=telegram` (по умолчанию) — аппрув в личке бота, кнопки A/B, правка реплаем.
Быстро для соло-пилота; нужен `ADMIN_CHAT_ID`.

`REVIEW_SURFACE=airtable` — аппрув в гриде: видно всё разом, статус дропдауном, правка в ячейке,
несколько ревьюеров. `chat_id НЕ нужен` (токен бота — только для постинга). Стор остаётся
единым источником правды, Airtable — окно ревью.

**Настройка базы Airtable** (таблица `Drafts`, поля):
`Draft ID` (text) · `Match` (text) · `GEO` (text) · `Channel` (text) · `Compliance` (text) ·
`Post A` (long text) · `Post B` (long text) · `Hook rationale` (text) ·
`Variant` (single select: A, B) · `Edited text` (long text) ·
`Status` (single select: Pending, Approved, Rejected) · `Processed` (checkbox) · `Published link` (url).
Менеджер: открывает грид, читает A/B, ставит `Variant` (или пишет `Edited text`), меняет `Status`
на Approved → пост уходит в канал, в `Published link` падает ссылка.

## 1) Прогнать СЕГОДНЯ (без GCP/Apify)

Нужны только: ключ Anthropic + бот Telegram + твой chat_id + публичный канал.

```bash
pip install -r requirements.txt
cp .env.example .env     # заполни ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID,
                         # LT_CHANNEL_ID, LT_BRAND/LT_AFFILIATE_URL/LT_PROMO_CODE
                         # оставь STORE_BACKEND=local SOURCE=sample METRICS_BACKEND=local

python -m src.review      # терминал 1 — ревью (telegram по умолчанию; для грида REVIEW_SURFACE=airtable)
python -m src.orchestrate  # терминал 2 — прогнать петлю на sample-матче
```

Боту в личку прилетит черновик (A/B + статус комплаенса). **✅ A/B** → пост в канал;
правка — **ответь (reply)** своим текстом. Менеджер = редактор-аппрувер, в гео руками не пишет.

> `ADMIN_CHAT_ID`: напиши боту, открой `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id`.
> Бота добавь админом публичного канала с правом постить.

## 2) Перевод в прод

- **Стор → BigQuery:** прогони `schema/bigquery.sql` (замени `<DATASET>`), `STORE_BACKEND=bigquery`,
  `BQ_DATASET=x-fabric-494718-d1.autopost`, `GOOGLE_APPLICATION_CREDENTIALS`. Реюз твоей инфры.
- **Источник → Apify:** `SOURCE=apify`, `APIFY_TOKEN`. В `config/sport/football.yaml` основной актор —
  `maximedupre/sofascore-live-events-scraper` (by-date, finished results, ~$0.90/1k). Сверь
  `field_map` с реальным выводом актора (одна строка датасета) и поправь пути при необходимости.
  Альтернативы (enabled:false): parseforge/sofascore, openligadb (явно покрывает ЧМ), flashscore.
- **Метрики → BigQuery:** `METRICS_BACKEND=bigquery`, `METRICS_TABLE`. Можно навести вьюху-адаптер
  на твой Voonix-стор по `subid LIKE 'tg_%'` (пример в `schema/bigquery.sql`).

## 3) Масштаб (Слой 4)

- **+ гео:** конфиги LV/ES/HR/PL уже в репо. Включаешь через `GEOS=lt,lv,es,hr,pl`. Слои 2–3 не трогаешь.
- **+ спорт:** добавь `config/sport/<sport>.yaml` + актор. Слой генерации тот же.

## 4) Петля обратной связи (моат)

Каждый пост уходит с уникальным `subid=tg_<geo>_<draft_id>` в трекинг-ссылке.
`feedback.py` джойнит published-посты с метриками по subid (clicks/FTD), агрегирует по хукам
и отдаёт «winners block» — он инжектится в промпт (`geo.learned_winners`). Чем больше гео и
объёма, тем точнее пишет. Отчёт: `python -m src.feedback lt`.

## 5) Деплой (Railway)

`Procfile`: `bot` — отдельный always-on сервис; `worker` (`python -m src.orchestrate`) — на
Railway Cron (напр. `*/15 * * * *`), событийный триггер через polling завершённых матчей.

## Где зашито качество

1. **Человек в петле** — редактор, не автор (bot.py).
2. **Конверсионная рубрика в промпте** — хук-вперёд, конкретика, CTA, A/B (prompts/).
3. **Комплаенс hard-stop** — промпт использует ТОЛЬКО реальные поля payload (защита от
   выдуманных цифр), обязателен блок 18+/RG/T&C; статус виден в превью аппрува.
4. **Обучение на данных** — победившие хуки усиливают качество при масштабе (feedback.py).

## Что от тебя нужно для прода

- Актуальные офферы по гео (бренд / трекинг-ссылка / промокод) → в `.env`.
- Сверить `field_map` Sofascore-актора с его реальным выводом (один прогон актора).
- Навести `METRICS_TABLE`/вьюху на твой Voonix-стор (колонка subid).
