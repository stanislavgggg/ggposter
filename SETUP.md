# Подключение «по-взрослому» — пошагово и просто

Пять блоков ниже **независимы** — можно в любом порядке. Но проще всего так:
**1) Apify → 2) BigQuery → 3) Airtable → 4) Email → 5) Гео → 6) 24/7.**

Все ключи живут в **одном файле `.env`** (скопируй его из `.env.example`).
Тексты и страны — в папке **`config/`**. Больше ничего в коде менять не надо.

> Как проверять каждый блок: после настройки запусти
> `python -m src.orchestrate` (создать черновики) и `python -m src.review` (приёмная).

---

## Блок 1 — Apify: настоящие матчи и новости (вместо тестовых)

**Зачем:** чтобы система тянула реальные результаты ЧМ и инфоповоды, а не фикстуру.

1. Зарегистрируйся на **apify.com**.
2. Открой → **Settings → Integrations** → скопируй свой **API token**.
3. В `.env` впиши:
   - `SOURCE=apify`
   - `APIFY_TOKEN=apify_api_...` (твой токен)
4. Зайди в стор Apify и добавь актор **`maximedupre/sofascore-live-events-scraper`** (это уже прописано в `config/sport/football.yaml`). Нажми у него **Try / Run** один раз.
   - Режим работы — **двухпроходный** (`fetch_mode: two_pass`, дёшево):
     **Проход 1** тянет список матчей за дату БЕЗ деталей (`includeMatchDetails: false`, `list_input`) — быстро.
     Из списка берётся **свежайший матч ЧМ, о котором ещё не писали**.
     **Проход 2** тянет детали (`includeMatchDetails: true`, режим `eventUrls`, `detail_input`) **только по этому одному матчу** → из его `incidents` берём FT/счёт/голы.
   - ⚠️ **Сверь `field_map.event_url`** (1 минута): открой результат прохода 1, возьми одну строку матча и проверь, в каком поле лежит URL матча Sofascore. По умолчанию `url`; если иначе — впиши путь в `field_map.event_url`. Без корректного URL проход 2 не сработает (в логах будет «нет URL матча» / «сверь field_map.event_url»).
   - `timeout_secs: 600`, `max_detail_lookups: 5` (сколько кандидатов максимум проверить во 2-м проходе), `EVENTS_PER_RUN` (сколько матчей за прогон, по умолчанию 1).
   - Откат на старое поведение: `FETCH_MODE=single_pass` (тянет детали по всем `maxItems` сразу — дорого/медленно).
   - На своём задеплоенном инпуте убери поле `statusFilter` — у этого актора его в схеме нет, оно игнорируется.
5. **Важная сверка** (1 минута): после прогона открой результат → возьми **одну строку** данных и посмотри, как там называются поля (`homeTeam`, `score` и т.д.). Если имена отличаются от тех, что в `config/sport/football.yaml` (блок `field_map`) — поправь пути там. Структуру вокруг трогать не нужно.
6. Новости: актор **`cloud9_ai/gdelt-news-scraper`** уже прописан в `config/news.yaml`, ключей не требует.
7. (Опц.) xG: в `.env` поставь `ENRICH=true` и в `config/sport/football.yaml` у `understat_xg` поставь `enabled: true`.

**Проверка:** `python -m src.orchestrate` → в логе видно `matches=…` и `news=…` больше нуля.

---

## Блок 2 — BigQuery: хранилище и метрики (твой стек)

**Зачем:** чтобы черновики и история не терялись, и чтобы работала «петля обучения» (какие посты дают депозиты).

1. В Google Cloud открой свой проект (у тебя уже есть `x-fabric-494718-d1`).
2. Создай **датасет** (например `autopost`) в BigQuery.
3. Открой файл `schema/bigquery.sql`, замени везде `<DATASET>` на `x-fabric-494718-d1.autopost`, и выполни его в BigQuery (создаст 2 таблицы).
4. Сделай **сервисный аккаунт**: IAM → Service Accounts → Create. Дай ему роли **BigQuery Data Editor** и **BigQuery Job User**. Создай **ключ JSON**, скачай файл.
5. В `.env`:
   - `STORE_BACKEND=bigquery`
   - `BQ_DATASET=x-fabric-494718-d1.autopost`
   - `GOOGLE_APPLICATION_CREDENTIALS=/путь/к/ключу.json`
6. **Метрики — это на потом, не сейчас.** Обучение по умолчанию выключено (`FEEDBACK_MODE=off`).
   Если захочешь, чтобы система подстраивалась — поставь `FEEDBACK_MODE=admin_choice`: она будет
   учиться на твоих аппрувах (что чаще одобряешь/правишь/отклоняешь), **без всяких метрик и BigQuery**.
   Полноценные метрики (`FEEDBACK_MODE=metrics`, по FTD/CTR из Voonix/MailMind через вьюху `post_metrics` —
   пример в `schema/bigquery.sql`) подключишь, когда настроишь атрибуцию. Сейчас можно пропустить.

---

## Блок 3 — Airtable: черновики в табличке (вместо лички)

**Зачем:** когда стран и постов много, грид удобнее ленты в Telegram — видно всё разом, правишь в ячейке.

1. Создай базу в **airtable.com**, в ней таблицу **`Drafts`**.
2. Создай поля (имена точно как тут):
   - `Draft ID` (text), `Kind` (text), `Match` (text), `GEO` (text), `Channel` (text)
   - `Compliance` (text), `Hook rationale` (text)
   - `Post A` (long text), `Post B` (long text)
   - `Subject A` (text), `Subject B` (text), `Preview text` (text), `Audience` (text) — это для email
   - `Variant` (**single select**: `A`, `B`)
   - `Edited text` (long text)
   - `Status` (**single select**: `Pending`, `Approved`, `Rejected`)
   - `Processed` (**checkbox**)
   - `Channel` (text) — ПОДСКАЗКА: бот сам впишет доступные назначения гео
   - `Channels` (text) — ВЫБОР менеджера: куда постить (метки через запятую, либо «все»). Пусто = пост не уходит (нет авторассылки)
   - `Published link` (text)
3. Получи доступ: **Account → Developer hub → Personal access tokens** → Create. Дай права (scopes) `data.records:read` и `data.records:write` и доступ к своей базе.
4. Узнай **Base ID**: открой базу, в адресе она начинается на `app...` — это он.
5. В `.env`:
   - `REVIEW_SURFACE=airtable`
   - `AIRTABLE_TOKEN=pat...`
   - `AIRTABLE_BASE_ID=app...`
   - `AIRTABLE_TABLE=Drafts`

**Как пользоваться:** черновики сами появляются строками со `Status=Pending`. Менеджер ставит `Variant` (A/B) или пишет свой текст в `Edited text`, в поле **`Channels`** перечисляет, куда постить (метки из подсказки `Channel`, через запятую, или «все»), и меняет `Status` на **Approved** → пост уходит в выбранные каналы/паблики, в `Published link` падают ссылки. Если `Channels` пусто — пост **не публикуется** (авторассылки нет), строка ждёт выбора. `chat_id` не нужен.

**Проверка:** `python -m src.review` + `python -m src.orchestrate` → в таблице появилась строка.

---

## Блок 4 — Email: рассылка через Mailchimp

**Зачем:** второй выход конвейера — письма по структуре HOOK→AGITATE→PROVE→OFFER→CTA, тёплым и холодным.

1. В **mailchimp.com** → **Account → Extras → API keys** → создай ключ (он оканчивается на `-us21` и т.п. — это «дата-центр», подставится сам).
2. Возьми **Audience ID** (List ID): Audience → Settings → «Unique id». (Опц.) сделай сегменты «тёплые/холодные» и возьми их id.
3. В `.env`:
   - `ESP_BACKEND=mailchimp`
   - `MAILCHIMP_API_KEY=...`
   - `MAILCHIMP_FROM_NAME=Имя отправителя`, `MAILCHIMP_REPLY_TO=почта@домен`
   - `LT_MC_LIST_ID=...`, `LT_MC_WARM_SEGMENT=...`, `LT_MC_COLD_SEGMENT=...`
4. Включи email-выход: в `config/geo/lt.yaml` поменяй
   `outputs: [telegram]` → **`outputs: [telegram, email]`**.
   А в блоке `email.audiences` выбери `[warm]` или `[warm, cold]`.

**Проверка без рассылки:** оставь `ESP_BACKEND=local` — письма будут сохраняться в `data/sent_emails/*.html`, можно открыть и посмотреть. Когда устроит — переключи на `mailchimp`.

> ⚠️ Mailchimp реально **отправит** кампанию по списку, как только менеджер нажмёт Approve. Сначала проверь на `local` и на маленьком тестовом сегменте.

---

## Блок 5 — Больше стран

**Зачем:** масштаб. Конфиги LT/LV/ES/HR/PL уже лежат в `config/geo/`.

1. Для каждой страны заполни в `.env` её данные:
   - канал(ы)/паблик(и): `LV_CHANNEL_ID` (основной) и опц. `LV_PUBLIC_ID` (паблик/группа), и так для каждой. Несколько назначений = блок `telegram.destinations` в `config/geo/<geo>.yaml` (можно дописать ещё строки).
   - оффер: `LV_BRAND` / `LV_AFFILIATE_URL` / `LV_PROMO_CODE`, и так для каждой.
   - (если email) — `LV_MC_LIST_ID` и т.д.
2. Включи нужные страны одной строкой:
   - `GEOS=lt,lv,es,hr,pl`
3. Новый спорт (например баскетбол для LT/LV) — скопируй `config/sport/football.yaml` в `basketball.yaml`, подставь баскетбольный актор, и `PILOT_SPORT=basketball`.

**Проверка:** `python -m src.orchestrate` → в логе по строке на каждую страну.

---

## Блок 6 — Поставить на 24/7 (Railway)

**Зачем:** чтобы крутилось само, а не пока включён твой ноут.

1. Залей проект в GitHub-репозиторий.
2. На **railway.app** → New Project → Deploy from GitHub.
3. В Railway → **Variables** перенеси все переменные из `.env`.
4. В проекте уже есть `Procfile` с тремя процессами — включи их:
   - **review** — держать всегда включённым (приёмная + отправка).
   - **worker** — повесить на **Cron** (Railway → Settings → Cron): `0 */3 * * *` — раз в 3 часа проверяет новые матчи/новости.
   - **webhook** (опц.) — чтобы реагировать мгновенно: в Apify у актора настрой вебхук «Run finished» на адрес `https://<твой-railway>/run?secret=<WEBHOOK_SECRET>`.

---

## Финальный чек-лист `.env`

```
# база
PILOT_GEO=lt   PILOT_SPORT=football   GEOS=lt,lv,es,hr,pl
EVENTS_PER_RUN=1   # сколько новых матчей брать за прогон (1 = первый, о котором ещё не писали; 0 = все)
ANTHROPIC_API_KEY=…   TELEGRAM_BOT_TOKEN=…(новый!)   ADMIN_CHAT_ID=…
# источники
SOURCE=apify   APIFY_TOKEN=…   ENRICH=true   APIFY_TIMEOUT_SECS=600
# хранилище + метрики
STORE_BACKEND=bigquery   BQ_DATASET=x-fabric-494718-d1.autopost   GOOGLE_APPLICATION_CREDENTIALS=…
METRICS_BACKEND=bigquery   METRICS_TABLE=x-fabric-494718-d1.autopost.post_metrics
# ревью
REVIEW_SURFACE=airtable   AIRTABLE_TOKEN=…   AIRTABLE_BASE_ID=…   AIRTABLE_TABLE=Drafts
# email
ESP_BACKEND=mailchimp   MAILCHIMP_API_KEY=…   MAILCHIMP_FROM_NAME=…   MAILCHIMP_REPLY_TO=…
# каналы и офферы по гео (CHANNEL_ID = основной, PUBLIC_ID = паблик/группа, опц.)
LT_CHANNEL_ID=…  LT_PUBLIC_ID=…  LT_BRAND=…  LT_AFFILIATE_URL=…  LT_PROMO_CODE=…  LT_MC_LIST_ID=…
LV_CHANNEL_ID=…  LV_PUBLIC_ID=…  LV_BRAND=…  …(и так для ES/HR/PL)
```

**Совет:** подключай по одному блоку и сразу проверяй (`orchestrate` + `review`).
Не включай настоящую Mailchimp-рассылку, пока не убедишься на `local`, что письма ок.
