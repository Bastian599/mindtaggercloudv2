# Jira Tickets & Worklogs – Streamlit App (All-in)

- PKCE (S256) mit `code_verifier` im verschlüsselten State
- Token-Exchange: `application/x-www-form-urlencoded`
- **Public** *oder* **Confidential** Client Support (Basic Auth wenn Secret gesetzt)
- Neon (Postgres) oder SQLite per `DATABASE_URL`

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

`.streamlit/secrets.toml` ausfüllen (siehe Template). In Streamlit Cloud **Secrets** in der UI setzen.

## Wichtig (Atlassian)

- Redirect-URI in der Developer Console **exakt** wie `ATLASSIAN_REDIRECT_URI`.
- App-Typ: Public (ohne Secret) **oder** Confidential (mit Secret).
- Scopes:
  `offline_access read:jira-user read:jira-work write:jira-work`
