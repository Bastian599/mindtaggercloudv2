# utils/health.py
import time
def run_health_checks(api, storage, auth):
    checks = {"jira_ok": False, "db_ok": False, "permissions": {}, "token_seconds_left": None}
    try:
        me = api.get_myself()
        checks["jira_ok"] = bool(me.get("accountId"))
        checks["jira_profile"] = me
    except Exception as e:
        checks["jira_ok"] = False
        checks["jira_profile"] = {"error": str(e)}

    db = storage.ping()
    checks["db_ok"] = db.get("ok", False)
    checks["db_info"] = db

    try:
        checks["permissions"] = api.get_permissions()
    except Exception as e:
        checks["permissions"] = {"error": str(e)}

    try:
        tok = auth._token or {}
        checks["token_seconds_left"] = int(tok.get("expires_at", 0) - time.time())
        if checks["token_seconds_left"] is not None and checks["token_seconds_left"] < 60:
            checks["refresh_ok"] = auth.refresh_token()
        else:
            checks["refresh_ok"] = True
    except Exception:
        checks["refresh_ok"] = False
    return checks
