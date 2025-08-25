# app.py (PL-Filter, single-select default, combined columns, 3 P-Label-Modi)
import streamlit as st
import pandas as pd
import os
from datetime import datetime
from utils.auth import AtlassianAuth
from utils.jira_api import JiraAPI, is_p_label, extract_p_labels, compute_new_labels
from utils.csv_utils import parse_worklog_csv, validate_worklog_rows
from utils.health import run_health_checks
from utils.storage import Storage

st.set_page_config(page_title="Jira Tickets & Worklogs", page_icon="🧩", layout="wide")

# --- load config from secrets or env ---
CFG = {
    "CLIENT_ID": st.secrets.get("ATLASSIAN_CLIENT_ID", os.getenv("ATLASSIAN_CLIENT_ID", "")),
    "CLIENT_SECRET": st.secrets.get("ATLASSIAN_CLIENT_SECRET", os.getenv("ATLASSIAN_CLIENT_SECRET", "")),
    "REDIRECT_URI": st.secrets.get("ATLASSIAN_REDIRECT_URI", os.getenv("ATLASSIAN_REDIRECT_URI", "")),
    "SCOPES": st.secrets.get("ATLASSIAN_SCOPES", os.getenv("ATLASSIAN_SCOPES", "offline_access read:jira-user read:jira-work write:jira-work")),
    "FERNET_KEY": st.secrets.get("FERNET_KEY", os.getenv("FERNET_KEY", "")),
    "DATABASE_URL": st.secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", "sqlite:///app.db")),
}

storage = Storage(db_url=CFG["DATABASE_URL"])

# --- sidebar ---
st.sidebar.title("⚙️ Einstellungen")
mode = "CONFIDENTIAL" if CFG["CLIENT_SECRET"] else "PUBLIC"
with st.sidebar.expander("Konfiguration (read-only)", expanded=False):
    safe_cfg = dict(CFG)
    if safe_cfg.get("CLIENT_SECRET"): safe_cfg["CLIENT_SECRET"] = "***"
    if safe_cfg.get("FERNET_KEY"): safe_cfg["FERNET_KEY"] = "***"
    st.code(safe_cfg, language="json")
st.sidebar.caption(f"OAuth-Client-Modus: {mode} | redirect: {CFG['REDIRECT_URI']}")

# --- Auth ---
auth = AtlassianAuth(
    client_id=CFG["CLIENT_ID"],
    client_secret=CFG["CLIENT_SECRET"],
    redirect_uri=CFG["REDIRECT_URI"],
    scopes=CFG["SCOPES"],
    fernet_key=CFG["FERNET_KEY"],
    storage=storage
)

user_email = st.sidebar.text_input("E-Mail (für SSO-State)", value=st.session_state.get("user_email",""))
if user_email:
    st.session_state["user_email"] = user_email

auth_area = st.sidebar.container()
with auth_area:
    auth.render_login_flow(user_email=user_email)

# After auth, Jira API client becomes available
if not auth.is_authenticated():
    st.title("🔐 Bitte anmelden")
    st.info("Melde dich mit Atlassian an, um Tickets zu sehen und Worklogs zu erfassen.")
    st.stop()

api = JiraAPI(auth)
profile = api.get_myself()
account_id = profile.get("accountId")
st.sidebar.success(f"Angemeldet als {profile.get('displayName', profile.get('emailAddress',''))}")
site_url = auth.get_cloud_url()
if site_url:
    st.sidebar.write(f"Cloud: {site_url}")

# --- Project list with PL filter ---
all_projects = api.get_projects()
def filter_projects_pl(projects, my_id):
    out = []
    for p in projects:
        lead = (p.get("lead") or {}).get("accountId")
        if lead and my_id and lead == my_id:
            out.append(p)
    # Fallback: wenn nichts erkannt, gib alle zurück
    return out if out else projects

only_pl = st.sidebar.checkbox("Nur Projekte, bei denen ich Projektleiter bin", value=True)
projects = filter_projects_pl(all_projects, account_id) if only_pl else all_projects
proj_map = {p['key']: f"{p['name']} ({p['key']})" for p in projects}

# Single-select standardmäßig, optional Multi
multi = st.sidebar.checkbox("Mehrere Projekte auswählen", value=False)
if not projects:
    st.warning("Keine Projekte gefunden (ggf. fehlen Berechtigungen).")
    st.stop()

if multi:
    selected_keys_global = st.sidebar.multiselect("Projekte", options=list(proj_map.keys()), format_func=lambda k: proj_map[k])
else:
    selected_single = st.sidebar.selectbox("Projekt", options=list(proj_map.keys()), format_func=lambda k: proj_map[k])
    selected_keys_global = [selected_single] if selected_single else []

# --- Tabs ---
tab_tickets, tab_plabels, tab_worklog, tab_csv, tab_health = st.tabs(
    ["📋 Tickets", "🏷️ P-Labels", "⏱️ Einzel-Worklog", "📥 CSV-Import", "🩺 Health-Check"]
)

# --------------- Tickets Tab -----------------
with tab_tickets:
    st.subheader("Tickets anzeigen & filtern")
    query = st.text_input("Volltextsuche (optional)", "")
    if selected_keys_global:
        with st.spinner("Tickets werden geladen..."):
            issues = api.search_issues(selected_keys_global, text=query, exclude_statuses=["Closed","Geschlossen","Abgebrochen"])
        if not issues:
            st.info("Keine Tickets gefunden.")
        else:
            rows = []
            for it in issues:
                fields = it.get("fields", {})
                labels = fields.get("labels", []) or []
                p_labels = extract_p_labels(labels)
                k = it["key"]
                link = f"{site_url}/browse/{k}" if site_url else ""
                rows.append({
                    "Ticket (P-Label)": f"{k} ({p_labels[0] if p_labels else '—'})",
                    "Summary": fields.get("summary",""),
                    "Status": fields.get("status",{}).get("name",""),
                    "Assignee": (fields.get("assignee") or {}).get("displayName",""),
                    "Öffnen": link
                })
            df = pd.DataFrame(rows)
            try:
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Öffnen": st.column_config.LinkColumn("Öffnen in Jira")}
                )
            except Exception:
                st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption("Hinweis: 'Closed/Geschlossen/Abgebrochen' wurden ausgeschlossen; 'Erledigt' bleibt sichtbar.")

# --------------- P-Labels Tab -----------------
with tab_plabels:
    st.subheader("P-Labels (Salesforce-Projektcode) zuweisen")
    st.caption("Format: **PXXXXXX** (eine führende 'P' + 6 Ziffern). Alte P-Labels werden vorher entfernt.")
    search_text = st.text_input("Volltextsuche (optional)", "", key="pl_search")
    if selected_keys_global:
        with st.spinner("Tickets werden geladen..."):
            issues_pl = api.search_issues(selected_keys_global, text=search_text, exclude_statuses=["Closed","Geschlossen","Abgebrochen"])
    else:
        issues_pl = []

    option = st.radio(
        "Modus für P-Label-Vergabe",
        ["1) Alle Tickets ohne P-Label versehen",
         "2) Alle Tickets auf neues P-Label setzen",
         "3) Einzeln ausgewählte Tickets"],
        index=0
    )
    new_plabel = st.text_input("Neues P-Label", placeholder="z. B. P123456", key="pl_value").strip().upper()
    if new_plabel and not is_p_label(new_plabel):
        st.error("Ungültiges Format. Erlaubt ist: P + 6 Ziffern (z. B. P123456).")

    candidates = []
    if issues_pl:
        if option.startswith("1"):
            candidates = [it for it in issues_pl if len(extract_p_labels(it["fields"].get("labels", []) or [])) == 0]
        elif option.startswith("2"):
            candidates = list(issues_pl)  # alle
        elif option.startswith("3"):
            # Tabelle mit auswählbaren Tickets
            table_rows = []
            for it in issues_pl:
                labels = it["fields"].get("labels", []) or []
                p = ", ".join(extract_p_labels(labels)) or "—"
                table_rows.append({
                    "Auswählen": False,
                    "Key": it["key"],
                    "P-Label": p,
                    "Summary": it["fields"].get("summary","")[:120],
                })
            edf = pd.DataFrame(table_rows)
            st.markdown("**Tickets auswählen:**")
            edf = st.data_editor(
                edf,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "Auswählen": st.column_config.CheckboxColumn("Auswählen"),
                }
            )
            picked_keys = set(edf[edf["Auswählen"] == True]["Key"].tolist())
            candidates = [it for it in issues_pl if it["key"] in picked_keys]

    col_prev, col_apply = st.columns(2)
    with col_prev:
        if st.button("👀 Vorschau erzeugen", disabled=not (candidates and is_p_label(new_plabel))):
            preview_rows = []
            for it in candidates:
                old_labels = it["fields"].get("labels", []) or []
                new_labels = compute_new_labels(old_labels, new_plabel)
                preview_rows.append({
                    "Key": it["key"],
                    "Summary": it["fields"].get("summary","")[:120],
                    "Alt-Labels": ", ".join(old_labels),
                    "Neu-Labels": ", ".join(new_labels),
                })
            st.session_state["pl_preview"] = pd.DataFrame(preview_rows)
        if "pl_preview" in st.session_state:
            st.dataframe(st.session_state["pl_preview"], use_container_width=True, hide_index=True)
    with col_apply:
        submit_apply = st.button("✅ Anwenden (Bulk)", type="primary", disabled="pl_preview" not in st.session_state or not is_p_label(new_plabel))
        if submit_apply and st.session_state.get("pl_preview") is not None:
            rows = st.session_state["pl_preview"].to_dict(orient="records")
            prog = st.progress(0, text="Aktualisiere Labels...")
            done, errs = 0, []
            total = len(rows)
            for idx, row in enumerate(rows, start=1):
                ok, err = api.update_issue_labels(row["Key"], row["Neu-Labels"].split(", ") if row["Neu-Labels"] else [])
                if not ok:
                    errs.append(f'{row["Key"]}: {err}')
                done += 1
                prog.progress(int(100*done/total), text=f"Aktualisiere Labels... ({done}/{total})")
            prog.empty()
            st.success(f"Fertig. {done - len(errs)}/{total} erfolgreich.")
            if errs:
                st.error("Fehler:\n" + "\n".join(errs))
            st.session_state.pop("pl_preview", None)

# --------------- Einzel-Worklog Tab -----------------
with tab_worklog:
    st.subheader("Einzel-Worklog erfassen")
    # Ticketauswahl nutzt globalen Projektfilter
    search_wl = st.text_input("Volltextsuche (optional)", "", key="wl_search")
    issue_choice = None
    if selected_keys_global:
        issues_wl = api.search_issues(selected_keys_global, text=search_wl, limit=50, exclude_statuses=[])
        if issues_wl:
            opts = {it["key"]: f"{it['key']} – {it['fields'].get('summary','')[:80]}" for it in issues_wl}
            issue_choice = st.selectbox("Ticket", options=list(opts.keys()), format_func=lambda k: opts[k])
    col1, col2, col3 = st.columns(3)
    with col1:
        date = st.date_input("Datum")
    with col2:
        start_time = st.time_input("Startzeit")
    with col3:
        dur_quarters = st.number_input("Dauer (in 15-Min-Schritten)", min_value=1, max_value=40, value=8, step=1)
    comment = st.text_input("Kommentar (optional)", "")
    c1, c2 = st.columns(2)
    with c1:
        submit_wl = st.button("📝 Worklog anlegen", type="primary", disabled=issue_choice is None)
    with c2:
        undo = st.button("↩️ Letzten Worklog rückgängig machen")
    if submit_wl and issue_choice:
        seconds = int(dur_quarters) * 15 * 60
        started_dt = pd.Timestamp.combine(pd.Timestamp(date), pd.Timestamp(start_time)).to_pydatetime()
        ok, res = api.add_worklog(issue_choice, started_dt, seconds, comment)
        if ok:
            st.success(f"Worklog erstellt (ID: {res.get('id')}).")
            storage.set_last_worklog(user_email or profile.get('emailAddress',''), res.get('id'), issue_choice)
        else:
            st.error(f"Fehler beim Anlegen: {res}")
    if undo:
        last = storage.get_last_worklog(user_email or profile.get('emailAddress',''))
        if last:
            ok, err = api.delete_worklog(last["issue_key"], last["worklog_id"])
            if ok:
                st.success(f"Letzter Worklog ({last['worklog_id']}) gelöscht.")
                storage.clear_last_worklog(user_email or profile.get('emailAddress',''))
            else:
                st.error(f"Löschen fehlgeschlagen: {err}")
        else:
            st.info("Kein letzter Worklog gefunden.")

# --------------- CSV Import Tab -----------------
with tab_csv:
    st.subheader("CSV-Import von Worklogs")
    st.caption("Format: Ticketnummer;Datum;benötigte Zeit in h;Uhrzeit;Beschreibung")
    uploaded = st.file_uploader("CSV auswählen", type=["csv"])
    if uploaded:
        df, parse_errors = parse_worklog_csv(uploaded)
        if parse_errors:
            st.error("Parser-Fehler:\n" + "\n".join(parse_errors))
        else:
            errs = validate_worklog_rows(df)
            if errs:
                st.error("Validierungsfehler:\n" + "\n".join(errs))
            st.dataframe(df, use_container_width=True, hide_index=True)
            if st.button("✅ Import starten", type="primary", disabled=bool(parse_errors or errs)):
                prog = st.progress(0, text="Import läuft...")
                results = []
                for i, row in df.iterrows():
                    issue_key = row["Ticketnummer"]
                    date = pd.to_datetime(row["Datum"], dayfirst=True).date()
                    t = pd.to_datetime(row["Uhrzeit"]).time()
                    raw_hours = float(str(row["benötigte Zeit in h"]).replace(',', '.'))
                    seconds = int(round(raw_hours * 3600 / 900) * 900)
                    if seconds <= 0:
                        seconds = 900
                    comment = str(row.get("Beschreibung","") or "")
                    started_dt = datetime.combine(date, t)
                    ok, res = api.add_worklog(issue_key, started_dt, seconds, comment)
                    results.append((issue_key, ok, res if not ok else res.get("id")))
                    prog.progress(int(100*(i+1)/len(df)), text=f"Import läuft... ({i+1}/{len(df)})")
                prog.empty()
                ok_count = sum(1 for _,ok,_ in results if ok)
                st.success(f"Import fertig: {ok_count}/{len(results)} erfolgreich.")
                fail = [(k, r) for k,ok,r in results if not ok]
                if fail:
                    st.error("Fehler:\n" + "\n".join([f"{k}: {r}" for k,r in fail]))

# --------------- Health Check Tab -----------------
with tab_health:
    st.subheader("System-Health")
    checks = run_health_checks(api=api, storage=storage, auth=auth)
    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Jira-Erreichbarkeit:** " + ("✅ OK" if checks['jira_ok'] else "❌ Fehler"))
        st.json(checks.get("jira_profile", {}))
    with cols[1]:
        st.markdown("**DB-Verbindung:** " + ("✅ OK" if checks['db_ok'] else "❌ Fehler"))
        st.json(checks.get("db_info", {}))
    st.markdown("---")
    st.markdown("**Berechtigungen:**")
    st.json(checks.get("permissions", {}))
    st.markdown("---")
    st.markdown("**SSO-Token:**")
    st.write(f"Läuft ab in: {checks.get('token_seconds_left', 'unbekannt')} Sekunden (Auto-Refresh < 60s).")
    if checks.get("refresh_ok") is False:
        st.error("Refresh-Test fehlgeschlagen.")
