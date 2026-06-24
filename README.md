# iGaming контент-конвейер (Telegram + Email) — по изначальному плану

Контент-менеджер перестаёт быть **автором** и становится **редактором-аппрувером**.
Один конвейер, два выхода: **авто-постинг в Telegram** и **email-конвейер конверсии**.
Пилот: футбол (ЧМ-2026) → Литва (LT). Масштаб = копирование конфига.

```
Слой 1: матчи (Apify) + новости (GDELT) + xG-enrich ─┐
Слой 2: Claude — посты (транскреация) И email          ─┤→ очередь аппрува → редактор ✅ → Слой 3
        (HOOK→AGITATE→PROVE→OFFER→CTA)                 ─┘                                  │
Слой 4: GEO/Sport/News-конфиг                                          ┌────────────────────┤
                                                                       ▼                    ▼
                                                          Telegram-канал           ESP (Mailchimp)
                                                                       └──────────┬─────────┘
                              обучение (опц.): на выборе админа (A/B, правки, отклонения) → стиль в промпт
```

## Полнота по изначальному плану

| План | Статус |
|---|---|
| Слой 1: Sofascore/FlashScore — матчи, события, статы | ✅ реальные акторы + field_map |
| Слой 1: акторы под ЧМ-2026 | ✅ openligadb (покрывает WC) |
| Слой 1: **xG (FBref/Understat)**, травмы (Transfermarkt) | ✅ `enrich.py` (Understat xG; ENRICH=true) |
| Слой 1: **новостной MCP-актор (Reuters/AP/BBC/GDELT, 65+ яз.)** | ✅ `cloud9_ai/gdelt-news-scraper`, type=news |
| Слой 1: событийный триггер (не daily-крон) | ✅ Apify webhook → `src.webhook` (+ крон как fallback) |
| Слой 2: посты — транскреация LT/LV/ES | ✅ мульти-гео |
| Слой 2: **email HOOK→AGITATE→PROVE→OFFER→CTA** | ✅ `email_generation.md` + `generate_email` |
| Слой 2: тёплый/холодный трафик | ✅ `audience: warm/cold` |
| Слой 2: прелендер→регистрация→FTD, A/B сабжекты | ✅ funnel + subject_a/b |
| Слой 3: авто-постинг Telegram | ✅ `publish.py` |
| Слой 3: email через ESP (Brevo/beehiiv/Mailchimp) | ✅ `esp.py` (Mailchimp + local) |
| Слой 3: очередь аппрува (Airtable ИЛИ бот) | ✅ Telegram-бот И Airtable-мостик |
| Слой 4: GEO/Sport-конфиг, масштаб строками | ✅ LT/LV/ES/HR/PL + sport/news |
| Петля обратной связи | ✅ `feedback.py`: 3 режима — `off` (сейчас) / `admin_choice` (выбор админа) / `metrics` (FTD/CTR, позже) |
| MVP: один спорт+гео, полная петля | ✅ + sample/local режимы |

## Структура

```
config/geo/{lt,lv,es,hr,pl}.yaml   # язык, тон, оффер, выходы, email-списки, КОМПЛАЕНС
config/sport/football.yaml         # Apify-акторы (+enrichers), стат-поля
config/news.yaml                   # GDELT-актор инфоповодов
prompts/post_generation.md         # Слой 2: посты (result/news)
prompts/email_generation.md        # Слой 2: email-конверсия
schema/bigquery.sql                # стор: match_events + post_drafts (telegram+email)
data/sample_match.json, sample_news.json, metrics.json   # для прогона без внешних API
src/
  ingest.py     # матчи (field_map) + новости (ingest_news), type=result|news
  enrich.py     # xG-долив (Understat/FBref), best-effort, ENRICH=true
  generate.py   # Claude -> черновик на каждый выход (telegram/email), мульти-гео
  review.py     # вход ревью: REVIEW_SURFACE=telegram|airtable
  bot.py / airtable_bridge.py   # поверхности аппрува (посты и письма)
  deliver.py    # маршрут доставки по kind: publish (канал) | send_email (ESP)
  publish.py / esp.py           # Слой 3: Telegram / Mailchimp(+local)
  feedback.py / metrics.py      # обучение: off | admin_choice | metrics (metrics использует metrics.py)
  orchestrate.py / webhook.py   # воркер (cron) / событийный триггер
  store.py      # единый источник правды: local (тест) | bigquery (прод)
```

## Каналы и паблики (несколько назначений на гео, выбор вручную)

В `config/geo/<geo>.yaml` блок `telegram.destinations` задаёт список назначений (каналы + паблики).
Невыставленные `${ENV}` отбрасываются — можно оставить одно. **Авторассылки нет**: при одобрении
поста менеджер сам выбирает, куда он уходит.

- Telegram-аппрув: жмёшь `✅ Опубликовать A` -> появляются кнопки назначений
  (каждый канал/паблик + «📢 Во все» + «✔️ Готово»). Тап по назначению = публикация туда;
  опубликованные помечаются ✓. Можно отправить в одно, в несколько или во все.
- Airtable-аппрув: в поле **Channels** менеджер перечисляет метки назначений (через запятую) или пишет «все».
  Поле **Channel** показывает доступные метки-подсказки. Пусто -> пост не публикуется (строка ждёт выбора).

subid различает канал: `tg_<geo>_<slug-назначения>_<draft_id>` (атрибуция по каналам не слипается).
История публикаций драфта (куда, msg_id, subid, ссылка) копится в `published_to`.

## Email как личное письмо (персона + подпись)

В `config/geo/<geo>.yaml` блок `email.sender_persona` и `email.sign_off` задают, от чьего лица
пишется письмо (как «Zlatan» в образце) и подпись в конце. Письмо генерируется как личное
сообщение эксперта (пик + бонус + короткий разбор + CTA + подпись), а не корпоративная рассылка.



`STORE_BACKEND=local SOURCE=sample METRICS_BACKEND=local ESP_BACKEND=local`. Нужны только
ANTHROPIC + Telegram (для постинга). Email-ветка в local пишет письма в `data/sent_emails/`.

```bash
pip install -r requirements.txt
cp .env.example .env       # заполни ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID, LT_*
python -m src.review       # терминал 1 — аппрув (telegram; для грида REVIEW_SURFACE=airtable)
python -m src.orchestrate  # терминал 2 — петля на sample-матче + sample-новости
```

## Включить email-ветку

В `config/geo/lt.yaml`: `outputs: [telegram, email]` (+ `email:` блок уже есть).
`ESP_BACKEND=mailchimp`, `MAILCHIMP_API_KEY`, и `LT_MC_LIST_ID/WARM_SEGMENT/...` в `.env`.
Тёплый/холодный — через `email.audiences: [warm, cold]`.

## Новости (инфоповоды) и xG

- Новости: `config/news.yaml` (GDELT-актор, темы, keyword-фильтр). `ingest_news()` кладёт их
  как события `type=news`; промпты ведут с новостного хука, используя только title+summary.
- xG: `ENRICH=true` + включи enricher в `config/sport/football.yaml`. Долив best-effort по именам
  команд; уже пришедшие поля не перетираются (никаких выдумок).

## Обучение (по желанию, `FEEDBACK_MODE`)

По умолчанию **выключено** (`off`) — система просто пишет по рубрике в промпте.
Когда захочешь, чтобы она подстраивалась — поставь `FEEDBACK_MODE=admin_choice`:
учится на **решениях менеджера** (что чаще одобряет как есть, что правит, что отклоняет, перекос A/B)
и подмешивает этот стиль в промпт. Никаких внешних метрик не нужно — только аппрувы, которые
и так происходят. Порог старта — `FEEDBACK_MIN_SAMPLES` решённых черновиков.
Режим `metrics` (по FTD/CTR из Voonix/MailMind) оставлен на потом — включишь, когда настроишь атрибуцию.
Отчёт: `python -m src.feedback lt telegram`.

## Прод и масштаб

- Стор/метрики → BigQuery (`schema/bigquery.sql`; метрики — вьюха на Voonix/MailMind по `subid LIKE 'tg_%'/'em_%'`).
- Источники → Apify (`SOURCE=apify`, `APIFY_TOKEN`; сверь `field_map` с выводом актора).
- Гео → `GEOS=lt,lv,es,hr,pl`. Спорт → новый `config/sport/<sport>.yaml`.
- Деплой Railway: `review` (always-on) + `worker` (Cron `0 */3 * * *`) + опц. `webhook` (Apify 'Actor finished' → `/run`).

## Где зашито качество

1. Человек в петле — редактор, не автор.
2. Конверсионная рубрика в промптах (хук-вперёд, конкретика, HOOK→AGITATE→PROVE→OFFER→CTA, A/B).
3. Комплаенс hard-stop: только реальные поля payload (защита от выдуманных цифр) + 18+/RG/T&C.
4. Обучение (опционально, FEEDBACK_MODE): по умолчанию off; admin_choice учится на выборе админа без внешних метрик; metrics — по FTD/CTR, когда настроена атрибуция.

## Airtable: поля таблицы `Drafts`

`Draft ID` · `Kind` · `Match` · `GEO` · `Channel` (подсказка: доступные назначения) ·
`Channels` (выбор менеджера: куда постить — метки через запятую, либо «все»; пусто = не публикуется) ·
`Compliance` · `Hook rationale` ·
`Post A`/`Post B` (для email в `Post A` — тело письма) ·
`Subject A`/`Subject B`/`Preview text`/`Audience` (email) ·
`Variant` (A/B) · `Edited text` · `Status` (Pending/Approved/Rejected) · `Processed` (checkbox) · `Published link`.

## От тебя для прода

- Актуальные офферы по гео + Mailchimp-списки/сегменты (warm/cold).
- Один прогон Sofascore- и GDELT-акторов, чтобы сверить `field_map`.
- `METRICS_TABLE`/вьюха на Voonix (telegram FTD) и MailMind (email open/CTR) по `subid`.
