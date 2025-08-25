# Jira Tickets & Worklogs – Streamlit App

Streamlit-App zur Verwaltung von Jira-Tickets und Zeiterfassungen mit Atlassian OAuth 2.0 (PKCE) und verschlüsselter Tokenablage.

## Features

- **P-Labels (Salesforce-Projektcode) zuweisen**
  - Formatprüfung `PXXXXXX`
  - Vorschau & Bestätigung
  - Bulk-Update mit Fortschrittsbalken
  - Entfernt alte P-Labels → maximal ein P-Label pro Ticket

- **Tickets anzeigen & filtern**
  - Auswahl mehrerer Projekte
  - „Closed/Geschlossen“ und „Abgebrochen“ ausgeschlossen
  - „Erledigt“ bleibt sichtbar
  - Ticket-Link öffnet Jira

- **Zeiterfassung**
  - **Einzel-Worklog:** Datum, Startzeit, Dauer (15-Min-Schritte), Kommentar, Undo
  - **CSV-Import:** `Ticketnummer;Datum;benötigte Zeit in h;Uhrzeit;Beschreibung` – Vorschau, Validierung, Import mit Progress

- **Health-Check**
  - Jira Reachability, DB-Ping, Berechtigungen, Token-Restlaufzeit mit Auto-Refresh

## Tech-Stack

- Python 3.10+ (empfohlen 3.11)
- Streamlit (aktuell), requests, pandas, cryptography (Fernet)
- SQLite oder Neon (Postgres) via `DATABASE_URL`

## Konfiguration (Secrets)

`.streamlit/secrets.toml`:
```toml
ATLASSIAN_CLIENT_ID = "..."
ATLASSIAN_CLIENT_SECRET = "..."
ATLASSIAN_REDIRECT_URI = "https://deine-app.streamlit.app"
ATLASSIAN_SCOPES = "offline_access read:jira-user read:jira-work write:jira-work"
FERNET_KEY = "<base64-32-byte-key>"

# SQLite (default)
DATABASE_URL = "sqlite:///app.db"
# Neon (Postgres)
# DATABASE_URL = "postgresql://<user>:<password>@<neon-host>/<db>?sslmode=require"
```

## Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deployment (Streamlit Cloud)

- Repo hochladen
- App erstellen; `Main file path: app.py`
- Secrets in der Cloud hinterlegen (siehe oben)
- Redirect-URI in Atlassian exakt auf Cloud-URL setzen

## Notes

- Bulk-Label-Update iterativ pro Issue (Jira Bulk-API kann ergänzt werden).
- Bei mehreren Atlassian-Clouds ggf. Auswahl-UI ergänzen.
