# utils/auth.py (PKCE + Public/Confidential)
import streamlit as st
import time, os, base64, json, requests
from urllib.parse import urlencode
from cryptography.fernet import Fernet, InvalidToken

AUTH_BASE = "https://auth.atlassian.com"
API_BASE  = "https://api.atlassian.com"

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _sha256(s: bytes) -> bytes:
    import hashlib
    return hashlib.sha256(s).digest()

class AtlassianAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, scopes: str, fernet_key: str, storage):
        self.client_id     = client_id
        self.client_secret = (client_secret or "").strip()  # optional bei PKCE/Public
        self.redirect_uri  = redirect_uri
        self.scopes        = scopes
        self.storage       = storage
        self.fernet        = Fernet(fernet_key) if fernet_key else None

        # Session wiederherstellen
        self._token = st.session_state.get("_oauth_token")
        self._cloud = st.session_state.get("_cloud")  # dict: id, url, name

        # OAuth Callback behandeln
        params = st.query_params.to_dict()
        if "code" in params and "state" in params:
            self._handle_callback(params["code"], params["state"])
            st.query_params.clear()

        # Auto-Refresh kurz vor Ablauf
        if self._token and (self._token.get("expires_at", 0) - int(time.time()) <= 60):
            self.refresh_token()

    # ---------- UI ----------
    def render_login_flow(self, user_email: str = ""):
        if not self.client_id or not self.redirect_uri:
            st.error("SSO nicht konfiguriert. Bitte CLIENT_ID und REDIRECT_URI setzen.")
            return
        if self.is_authenticated():
            c1, c2 = st.columns(2)
            with c1:
                st.success("Eingeloggt")
                if self._cloud:
                    st.caption(f"Cloud: {self._cloud.get('name')}")
            with c2:
                if st.button("Abmelden"):
                    self.logout()
            return

        # PKCE vorbereiten
        verifier_bytes = os.urandom(64)
        code_verifier  = _b64url(verifier_bytes)                # base64url ohne '='
        code_challenge = _b64url(_sha256(code_verifier.encode("ascii")))
        # optional zusätzlich im SessionState (Fallback)
        st.session_state["pkce_verifier"] = code_verifier

        if not self.fernet:
            st.error("FERNET_KEY fehlt – kann OAuth-State nicht schützen.")
            return

        # State verschlüsseln inkl. code_verifier
        state_obj = {
            "email": user_email or "unknown",
            "verifier": _b64url(os.urandom(16)),
            "ts": int(time.time()),
            "code_verifier": code_verifier,
        }
        enc_state = self.fernet.encrypt(json.dumps(state_obj).encode("utf-8")).decode("ascii")

        qs = {
            "audience": "api.atlassian.com",
            "client_id": self.client_id,
            "scope": self.scopes,
            "redirect_uri": self.redirect_uri,
            "state": enc_state,
            "response_type": "code",
            "prompt": "consent",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{AUTH_BASE}/authorize?{urlencode(qs)}"
        st.link_button("Mit Atlassian anmelden", auth_url)

    # ---------- State ----------
    def is_authenticated(self) -> bool:
        return bool(self._token and self._token.get("access_token"))

    def logout(self):
        st.session_state.pop("_oauth_token", None)
        st.session_state.pop("_cloud", None)
        self._token = None
        self._cloud = None

    # ---- Token POST helper ----
    def _post_token(self, data: dict):
        url = f"{AUTH_BASE}/oauth/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        if self.client_secret:
            # Confidential client → Basic Auth
            b64 = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {b64}"
            data = {k: v for k, v in data.items() if k != "client_secret"}
        else:
            # Public client → client_id in body
            data["client_id"] = self.client_id

        return requests.post(url, data=data, headers=headers, timeout=30)

    def _handle_callback(self, code: str, enc_state: str):
        # State entschlüsseln + TTL prüfen
        try:
            state_json = self.fernet.decrypt(enc_state.encode("ascii"), ttl=600).decode("utf-8")
            state = json.loads(state_json)
        except InvalidToken:
            st.error("Ungültiger oder abgelaufener OAuth-State. Bitte erneut einloggen.")
            return

        # code_verifier aus STATE (Fallback: Session)
        code_verifier = state.get("code_verifier") or st.session_state.get("pkce_verifier", "")
        if not code_verifier:
            st.error("PKCE code_verifier fehlt. Bitte Login erneut starten.")
            return

        # Token-Austausch
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }
        r = self._post_token(data)
        if r.status_code != 200:
            st.warning({
                "mode": "CONFIDENTIAL" if self.client_secret else "PUBLIC",
                "redirect_uri_used": self.redirect_uri,
                "has_code_verifier": True,
            })
            st.error(f"Token-Austausch fehlgeschlagen: {r.status_code} {r.text}")
            return

        tok = r.json()
        now = int(time.time())
        tok["expires_at"] = now + int(tok.get("expires_in", 3600))

        # Cloud-Ressourcen auflösen
        headers = {"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json"}
        rr = requests.get(f"{API_BASE}/oauth/token/accessible-resources", headers=headers, timeout=30)
        if rr.status_code != 200 or not rr.json():
            st.error("Konnte Atlassian-Cloud-Ressourcen nicht abrufen.")
            return
        cloud = rr.json()[0]  # ggf. UI für Auswahl ergänzen

        st.session_state["_oauth_token"] = tok
        st.session_state["_cloud"] = {"id": cloud["id"], "url": cloud.get("url",""), "name": cloud.get("name","")}
        self._token = tok
        self._cloud = st.session_state["_cloud"]

        # persistieren
        email = state.get("email","unknown")
        self.storage.save_oauth(email=email, token=tok, cloud=self._cloud)

    def refresh_token(self) -> bool:
        if not self._token or not self._token.get("refresh_token"):
            return False
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._token["refresh_token"],
        }
        r = self._post_token(data)
        if r.status_code != 200:
            return False
        tok = r.json()
        now = int(time.time())
        tok["expires_at"] = now + int(tok.get("expires_in", 3600))
        st.session_state["_oauth_token"] = tok
        self._token = tok
        self.storage.update_oauth_token(tok)
        return True

    # ---------- Helpers ----------
    def get_cloud_id(self) -> str:
        return (self._cloud or {}).get("id","")

    def get_cloud_url(self) -> str:
        return (self._cloud or {}).get("url","")

    def get_headers(self) -> dict:
        if not self._token:
            return {}
        if self._token.get("expires_at", 0) - int(time.time()) <= 60:
            self.refresh_token()
        return {
            "Authorization": f"Bearer {self._token['access_token']}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
