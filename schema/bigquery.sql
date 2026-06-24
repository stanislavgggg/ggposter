-- ============================================================
--  BigQuery schema (Слой 1 стор + очередь аппрува)
--  Замени <DATASET> на свой, напр. x-fabric-494718-d1.autopost
-- ============================================================

-- Нормализованный стор спортивных событий (накопительный, MERGE-upsert)
CREATE TABLE IF NOT EXISTS `<DATASET>.match_events` (
  match_id      STRING NOT NULL,     -- PK (id из источника)
  sport         STRING,
  league        STRING,
  status        STRING,              -- finished / live / scheduled
  home          STRING,
  away          STRING,
  score_home    INT64,
  score_away    INT64,
  key_stats     JSON,                -- только реально пришедшие поля
  raw           JSON,                -- сырой payload актора (на всякий)
  finished_at   TIMESTAMP,
  ingested_at   TIMESTAMP
);

-- Очередь черновиков / аппрува. Человек = редактор, не автор.
CREATE TABLE IF NOT EXISTS `<DATASET>.post_drafts` (
  draft_id            STRING NOT NULL,   -- PK
  match_id            STRING,
  geo                 STRING,
  channel_id          STRING,
  status              STRING,            -- pending / approved / rejected / published
  post_a              STRING,            -- вариант A
  post_b              STRING,            -- вариант B (A/B хук)
  chosen_variant      STRING,            -- A / B (что выбрал/отредактировал менеджер)
  edited_text         STRING,            -- если менеджер правил руками
  hook_rationale      STRING,
  compliance_ok       BOOL,
  compliance_notes    STRING,
  claude_model        STRING,
  notified            BOOL,              -- отправлен ли драфт админу в Telegram
  subid               STRING,            -- per-post метка трекинга (tg_<geo>_<draft_id>)
  hook                STRING,            -- первая строка опубликованного текста (для петли обучения)
  published_text      STRING,            -- финальный текст с подставленной ссылкой
  airtable_record_id  STRING,            -- id записи в Airtable (если ревью через Airtable)
  created_at          TIMESTAMP,
  decided_at          TIMESTAMP,
  published_at        TIMESTAMP,
  published_msg_id    INT64
);

-- Idempotency: один матч -> один драфт на гео.
-- Перед генерацией проверяем, нет ли уже драфта (см. store.event_has_draft).

-- ============================================================
--  Петля обратной связи (feedback): метрики по subid.
--  metrics.py (METRICS_BACKEND=bigquery) ждёт таблицу с колонками
--  subid / clicks / ftd (имена настраиваются через env METRICS_*_COL).
--  Это может быть твой существующий Voonix/FTD-стор. Пример вьюхи-адаптера:
--
--  CREATE OR REPLACE VIEW `<DATASET>.post_metrics` AS
--  SELECT subid, SUM(clicks) AS clicks, SUM(ftd) AS ftd
--  FROM `x-fabric-494718-d1.datasetmailchimp.VoonixChannelDaily`
--  WHERE subid LIKE 'tg_%'
--  GROUP BY subid;
-- ============================================================
