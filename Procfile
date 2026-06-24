# Railway: два процесса
# review — всегда-включённый: очередь аппрува + публикация (Telegram ИЛИ Airtable)
# worker — по Railway Cron (напр. */15 * * * *): ingest+generate по всем GEO
review: python -m src.review
worker: python -m src.orchestrate
