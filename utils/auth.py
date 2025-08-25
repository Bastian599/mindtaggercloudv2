# utils/auth.py
import streamlit as st
import time, os, base64, hashlib, json, requests
from urllib.parse import urlencode
from cryptography.fernet import Fernet, InvalidToken
from .storage import Storage

AUTH_BASE = "https://auth.atlassian.com"
API_BASE = "https://api.atlassian.com"

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _sha256(s: bytes) -> bytes:
    import hashlib
    return hashlib.sha256(s).digest()

class AtlassianAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, scopes: str, fernet_key: str, storage: Storage):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.storage = storage
        self.fernet = Fernet(fernet_key) if fernet_key else None

        # Restore session
        self._token = st.session_state.get("_oauth_token")
        self._cloud = st.session_state.get("_cloud")  # dict: id, url, name

        # Handle callback if code/state in query params
        params = st.query_params.to_dict()
        if "code" in params and "state" in params:
            self._handle_callback(params["code"], params["state"])
            # Clear query params
            st.query_params.clear()

        # Ensure token refresh if near expiry
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
        # Not authed: show "Sign in" link
        verifier = os.urandom(64)
        code_verifier = _b64url(verifier)
        code_challenge = _b64url(_sha256(code_verifier.encode("ascii")))
        st.session_state["pkce_verifier"] = code_verifier
        state_obj = {"email": user_email or "unknown", "verifier": _b64url(os.urandom(16)), "ts": int(time.time())}
        if not self.fernet:
            st.error("FERNET_KEY fehlt – kann OAuth-State nicht schützen.")
            return
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

    def _handle_callback(self, code: str, enc_state: str):
        # Decrypt & validate state
        try:
            state_json = self.fernet.decrypt(enc_state.encode("ascii"), ttl=600).decode("utf-8")
            state = json.loads(state_json)
        except InvalidToken:
            st.error("Ungültiger oder abgelaufener OAuth-State.")
            return
        # Exchange code
        verifier = state.get("code_verifier") or st.session_state.get("pkce_verifier", "")
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }
        r = requests.post(f"{AUTH_BASE}/oauth/token", json=data, timeout=30)
        if r.status_code != 200:
            st.error(f"Token-Austausch fehlgeschlagen: {r.status_code} {r.text}")
            return
        tok = r.json()
        now = int(time.time())
        tok["expires_at"] = now + int(tok.get("expires_in", 3600))
        # discover cloud resources
        headers = {"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json"}
        rr = requests.get(f"{API_BASE}/oauth/token/accessible-resources", headers=headers, timeout=30)
        if rr.status_code != 200 or not rr.json():
            st.error("Konnte Atlassian-Cloud-Ressourcen nicht abrufen.")
            return
        cloud = rr.json()[0]  # pick first; user can extend to choose later
        st.session_state["_oauth_token"] = tok
        st.session_state["_cloud"] = {"id": cloud["id"], "url": cloud.get("url",""), "name": cloud.get("name","")}
        self._token = tok
        self._cloud = st.session_state["_cloud"]
        # Persist tokens per user (by email in state)
        email = state.get("email","unknown")
        self.storage.save_oauth(email=email, token=tok, cloud=self._cloud)

    def refresh_token(self) -> bool:
        if not self._token or not self._token.get("refresh_token"):
            return False
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self._token["refresh_token"],
        }
        r = requests.post(f"{AUTH_BASE}/oauth/token", json=data, timeout=30)
        if r.status_code != 200:
            return False
        tok = r.json()
        now = int(time.time())
        tok["expires_at"] = now + int(tok.get("expires_in", 3600))
        # Keep same cloud
        st.session_state["_oauth_token"] = tok
        self._token = tok
        # update storage if available for user_email known from storage last record
        self.storage.update_oauth_token(tok)
        return True

    # ---------- Helpers for API ----------
    def get_cloud_id(self) -> str:
        return (self._cloud or {}).get("id","")

    def get_cloud_url(self) -> str:
        return (self._cloud or {}).get("url","")

    def get_headers(self) -> dict:
        if not self._token:
            return {}
        # Auto-refresh if expiring
        import time
        if self._token.get("expires_at", 0) - int(time.time()) <= 60:
            self.refresh_token()
        return {"Authorization": f"Bearer {self._token['access_token']}", "Accept": "application/json", "Content-Type": "application/json"}
