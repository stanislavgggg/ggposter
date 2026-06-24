# Railway процессы:
# review  — always-on: очередь аппрува + доставка (Telegram ИЛИ Airtable)
# worker  — по Railway Cron (0 */3 * * *  = раз в 3 часа): ingest(матчи+новости)+enrich+generate
# webhook — опц. событийный триггер: Apify 'Actor finished' -> POST /run -> run_once
review:  python -m src.review
worker:  python -m src.orchestrate
webhook: python -m src.webhook
