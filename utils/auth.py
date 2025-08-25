# utils/auth.py
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
        self.client_secret = client_secret or ""   # optional bei PKCE
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
        code_verifier  = _b64url(verifier_bytes)                # 86 Zeichen, base64url ohne '='
        code_challenge = _b64url(_sha256(code_verifier.encode("ascii")))
        # (Optional) im SessionState ablegen – aber wir relyen NICHT darauf:
        st.session_state["pkce_verifier"] = code_verifier

        if not self.fernet:
            st.error("FERNET_KEY fehlt – kann OAuth-State nicht schützen.")
            return

        # State verschlüsseln inkl. code_verifier
        state_obj = {
            "email": user_email or "unknown",_
