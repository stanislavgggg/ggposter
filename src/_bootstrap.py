"""
Бутстрап окружения. Импортируется ПЕРВЫМ в точках входа (orchestrate / review / webhook),
до любого обращения к BigQuery.

Зачем: google.cloud.bigquery.Client() читает креды из файла по пути
GOOGLE_APPLICATION_CREDENTIALS. На Railway файл секрета не примонтируешь —
поэтому весь JSON сервис-аккаунта кладётся в env GCP_SA_JSON (сырой JSON или base64),
а здесь он на старте пишется в /tmp/sa.json и путь выставляется в окружение.

Безопасно при любом бэкенде: если GCP_SA_JSON не задан — модуль ничего не делает,
поэтому local-стор и SOURCE=sample работают как раньше.
"""
import os
import json
import base64
import pathlib

_CREDS_PATH = os.environ.get("SA_CREDS_PATH", "/tmp/sa.json")


def _load_sa_json(raw: str):
    """raw -> dict сервис-аккаунта. Принимает сырой JSON ИЛИ base64(JSON)."""
    raw = raw.strip()
    if not raw.startswith("{"):
        # вероятно base64 (так удобнее хранить однострочником без экранирования)
        try:
            raw = base64.b64decode(raw).decode("utf-8").strip()
        except Exception as e:
            raise ValueError(f"GCP_SA_JSON не JSON и не валидный base64: {e}")
    return json.loads(raw)  # бросит при битом JSON — пусть падает явно


def init_gcp_credentials():
    """env GCP_SA_JSON -> файл -> GOOGLE_APPLICATION_CREDENTIALS. Идемпотентно."""
    raw = os.environ.get("GCP_SA_JSON")
    if not raw:
        return None  # нечего делать (local/sample)

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and \
            os.path.exists(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]):
        return os.environ["GOOGLE_APPLICATION_CREDENTIALS"]  # уже настроено явным файлом

    sa = _load_sa_json(raw)  # валидация: гарантируем, что это парсится
    path = pathlib.Path(_CREDS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sa), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # на некоторых ФС chmod недоступен — не критично
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    print(f"[bootstrap] GCP creds -> {path} (project={sa.get('project_id', '?')})")
    return str(path)


# выполняется при импорте — поэтому импорт должен идти ПЕРВЫМ в точке входа
init_gcp_credentials()
