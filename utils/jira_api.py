# utils/jira_api.py
import re, requests
from typing import List, Tuple, Any
from datetime import datetime, timezone
from .auth import API_BASE
import streamlit as st

P_LABEL_RE = re.compile(r"^P\d{6}$")

def is_p_label(s: str) -> bool:
    return bool(P_LABEL_RE.match((s or "").strip()))

def extract_p_labels(labels: List[str]) -> List[str]:
    return [l for l in (labels or []) if is_p_label(l)]

def strip_p_labels(labels: List[str]) -> List[str]:
    return [l for l in (labels or []) if not is_p_label(l)]

def compute_new_labels(old_labels: List[str], new_plabel: str) -> List[str]:
    return strip_p_labels(old_labels) + ([new_plabel] if new_plabel else [])

class JiraAPI:
    def __init__(self, auth):
        self.auth = auth

    def _url(self, path: str) -> str:
        cloud = self.auth.get_cloud_id()
        return f"{API_BASE}/ex/jira/{cloud}{path}"

    def _req(self, method: str, path: str, **kwargs):
        headers = self.auth.get_headers()
        r = requests.request(method, self._url(path), headers=headers, timeout=40, **kwargs)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            return False, detail
        try:
            return True, r.json()
        except Exception:
            return True, {}

    # ---- Basic ----
    def get_myself(self) -> dict:
        ok, res = self._req("GET", "/rest/api/3/myself")
        return res if ok else {}

    def get_projects(self) -> List[dict]:
        ok, res = self._req("GET", "/rest/api/3/project/search")
        if not ok:
            return []
        return res.get("values", [])

    def search_issues(self, project_keys: List[str], text: str = "", limit: int = 100, exclude_statuses: List[str] = None) -> List[dict]:
        exclude_statuses = exclude_statuses or []
        jql = f'project in ({",".join(project_keys)})'
        if text:
            jql += f' AND text ~ "{text}"'
        if exclude_statuses:
            ex = ','.join([f'"{s}"' for s in exclude_statuses])
            jql += f' AND status NOT IN ({ex})'
        params = {
            "jql": jql,
            "maxResults": min(limit, 200),
            "fields": "summary,status,assignee,labels"
        }
        ok, res = self._req("GET", "/rest/api/3/search", params=params)
        if not ok:
            st.warning(f"Suche fehlgeschlagen: {res}")
            return []
        return res.get("issues", [])

    # ---- Labels ----
    def update_issue_labels(self, issue_key: str, labels: List[str]) -> Tuple[bool, Any]:
        payload = {"update": {"labels": [{"set": labels}]}}  # set replaces
        return self._req("PUT", f"/rest/api/3/issue/{issue_key}", json=payload)

    # ---- Worklogs ----
    def add_worklog(self, issue_key: str, started_dt: datetime, seconds: int, comment: str) -> Tuple[bool, Any]:
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        started_str = started_dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")
        payload = {
            "started": started_str,
            "timeSpentSeconds": int(seconds),
        }
        if (comment or "").strip():
            payload["comment"] = comment
        return self._req("POST", f"/rest/api/3/issue/{issue_key}/worklog", json=payload)

    def delete_worklog(self, issue_key: str, worklog_id: str) -> Tuple[bool, Any]:
        return self._req("DELETE", f"/rest/api/3/issue/{issue_key}/worklog/{worklog_id}")

    # ---- Permissions ----
    def get_permissions(self) -> dict:
        ok, res = self._req("GET", "/rest/api/3/mypermissions")
        return res if ok else {}
