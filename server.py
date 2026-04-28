#!/usr/bin/env python3
"""
Objectif+ — Serveur local multi-postes
=======================================
Lance ce script sur UN poste du réseau, puis ouvre l'URL affichée depuis
n'importe quel autre poste de la même installation.

Usage : python server.py
Arrêt  : Ctrl+C
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, hashlib, hmac, secrets, time, re
from urllib.parse import urlparse

PORT     = 5001
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE= os.path.join(DATA_DIR, "app_data.json")
HTML_FILE= os.path.join(BASE_DIR, "V5_objectifplus.html")

# Sessions en mémoire  {token: {"user": {...}, "expires": timestamp}}
SESSIONS = {}
SESSION_TTL = 8 * 3600       # 8 heures

# Codes reset en mémoire  {userId: {"code": str, "expires": ts, "reset_token": str}}
RESET_TOKENS = {}
RESET_TTL = 30 * 60          # 30 minutes


# ── Mots de passe ──────────────────────────────────────────────────────────────

def hash_password(pwd: str) -> str:
    """PBKDF2-HMAC-SHA256 (stdlib, aucun pip requis)."""
    salt = secrets.token_hex(16)
    dk   = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 260_000)
    return f"pbkdf2:{salt}:{dk.hex()}"

def check_password(pwd: str, hashed: str) -> bool:
    if not hashed or not hashed.startswith("pbkdf2:"):
        return False
    try:
        _, salt, stored = hashed.split(":", 2)
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 260_000)
        return hmac.compare_digest(dk.hex(), stored)
    except Exception:
        return False


# ── Persistance ────────────────────────────────────────────────────────────────

def _default_data():
    return {
        "users": [{
            "id": 1,
            "login": "admin",
            "mdp": hash_password("Admin2025!"),
            "role": "admin",
            "prenom": "Administrateur",
            "structureId": None,
            "avatar": "⚙️",
            "couleur": "#7c3aed"
        }],
        "objectifs":   [],
        "recompenses": [],
        "echanges":    [],
        "structures":  []
    }

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    data = _default_data()
    _save_data(data)
    return data

def _save_data(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def strip_passwords(users: list) -> list:
    """Renvoie la liste des utilisateurs sans le champ mdp."""
    return [{k: v for k, v in u.items() if k != "mdp"} for u in users]


# ── Sessions ───────────────────────────────────────────────────────────────────

def get_session(token: str):
    if not token:
        return None
    sess = SESSIONS.get(token)
    if not sess or sess["expires"] < time.time():
        SESSIONS.pop(token, None)
        return None
    return sess["user"]


# ── Gestionnaire HTTP ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Journalisation silencieuse

    # ── Helpers de réponse ────────────────────────────────────────────────────

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _ok(self, data=None, status=200):
        body = json.dumps(data or {"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg: str, status=400):
        self._ok({"ok": False, "error": msg}, status)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def _token(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    # ── Méthodes HTTP ─────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/api/data":
            d = load_data()
            self._ok({
                "users":       strip_passwords(d["users"]),
                "objectifs":   d["objectifs"],
                "recompenses": d["recompenses"],
                "echanges":    d["echanges"],
                "structures":  d["structures"],
            })
        elif path == "/manifest.json":
            self._serve_static(os.path.join(BASE_DIR, "manifest.json"), "application/json")
        elif path == "/sw.js":
            self._serve_static(os.path.join(BASE_DIR, "sw.js"), "application/javascript")
        elif path.startswith("/icons/"):
            fname = path[7:]
            mime  = "image/svg+xml" if fname.endswith(".svg") else "image/png"
            self._serve_static(os.path.join(BASE_DIR, "icons", fname), mime)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        b    = self._body()

        if   path == "/api/auth":               self._auth(b)
        elif path == "/api/data":               self._save_data(b)
        elif path == "/api/users":              self._create_user(b)
        elif path == "/api/change-password":    self._change_password(b)
        elif path == "/api/verify-reset-code":  self._verify_reset(b)
        elif path == "/api/reset-password":     self._reset_password(b)
        else:
            m = re.match(r"^/api/users/(\d+)/(set-password|gen-reset-code)$", path)
            if m:
                uid    = int(m.group(1))
                action = m.group(2)
                if   action == "set-password":    self._set_password(uid, b)
                elif action == "gen-reset-code":  self._gen_reset_code(uid, b)
            else:
                self.send_response(404)
                self.end_headers()

    # ── Endpoints ─────────────────────────────────────────────────────────────

    def _serve_static(self, filepath, mime):
        if not os.path.exists(filepath):
            self.send_response(404); self.end_headers(); return
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type",   mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        with open(HTML_FILE, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self, b):
        login = b.get("login", "").strip().lower()
        mdp   = b.get("mdp",   "")
        d     = load_data()
        user  = next((u for u in d["users"] if u["login"].lower() == login), None)
        if user and check_password(mdp, user.get("mdp", "")):
            safe  = {k: v for k, v in user.items() if k != "mdp"}
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = {"user": safe, "expires": time.time() + SESSION_TTL}
            self._ok({"ok": True, "user": safe, "token": token})
        else:
            self._err("Identifiant ou mot de passe incorrect.", 401)

    def _save_data(self, b):
        """Sauvegarde les données ; le champ mdp des utilisateurs est IGNORÉ
        (les mots de passe ne transitent jamais par cet endpoint)."""
        d = load_data()
        if "users" in b:
            existing   = {u["id"]: u for u in d["users"]}
            merged     = []
            seen_ids   = set()
            for u in b["users"]:
                uid = u.get("id")
                ex  = existing.get(uid)
                if ex:
                    nu = dict(ex)                          # conserve le mdp haché
                    for k, v in u.items():
                        if k != "mdp":
                            nu[k] = v                     # met à jour tous sauf mdp
                    merged.append(nu)
                    seen_ids.add(uid)
            # Conserver les utilisateurs absents du payload (ex : créés depuis un autre poste)
            for uid, eu in existing.items():
                if uid not in seen_ids:
                    merged.append(eu)
            d["users"] = merged
        for k in ("objectifs", "recompenses", "echanges", "structures"):
            if k in b:
                d[k] = b[k]
        _save_data(d)
        self._ok()

    def _create_user(self, b):
        """Crée un utilisateur avec mot de passe haché (admin/éducateur seulement)."""
        requester = get_session(self._token())
        if not requester or requester.get("role") not in ("admin", "educateur"):
            return self._err("Non autorisé.", 403)
        if requester["role"] == "educateur" and b.get("role") != "jeune":
            return self._err("Les éducateurs peuvent uniquement créer des comptes jeunes.", 403)

        login = b.get("login", "").strip().lower()
        mdp   = b.get("mdp",   "")
        if not login or not mdp:
            return self._err("Identifiant et mot de passe obligatoires.")

        d = load_data()
        if any(u["login"].lower() == login for u in d["users"]):
            return self._err("Cet identifiant existe déjà.")

        nu = {
            "id":          int(time.time() * 1000),
            "login":       login,
            "mdp":         hash_password(mdp),
            "role":        b.get("role", "jeune"),
            "prenom":      b.get("prenom", ""),
            "nom":         b.get("nom",    ""),
            "age":         b.get("age")    or None,
            "avatar":      b.get("avatar", "👤"),
            "couleur":     b.get("couleur","#7c3aed"),
            "structureId": b.get("structureId") or None,
        }
        d["users"].append(nu)
        _save_data(d)
        safe = {k: v for k, v in nu.items() if k != "mdp"}
        self._ok({"ok": True, "user": safe})

    def _set_password(self, uid: int, b):
        """Admin ou éducateur réinitialise directement le mot de passe d'un jeune."""
        requester = get_session(self._token())
        if not requester or requester.get("role") not in ("admin", "educateur"):
            return self._err("Non autorisé.", 403)

        new_mdp = b.get("newMdp", "")
        if len(new_mdp) < 6:
            return self._err("Au moins 6 caractères requis.")

        d    = load_data()
        user = next((u for u in d["users"] if u["id"] == uid), None)
        if not user:
            return self._err("Utilisateur introuvable.", 404)
        if requester["role"] == "educateur" and user["role"] != "jeune":
            return self._err("Les éducateurs ne peuvent réinitialiser que le mot de passe des jeunes.", 403)

        user["mdp"] = hash_password(new_mdp)
        _save_data(d)
        self._ok()

    def _change_password(self, b):
        """L'utilisateur connecté change son propre mot de passe."""
        requester = get_session(self._token())
        if not requester:
            return self._err("Non authentifié.", 401)

        d    = load_data()
        user = next((u for u in d["users"] if u["id"] == requester["id"]), None)
        if not user or not check_password(b.get("currentMdp", ""), user.get("mdp", "")):
            return self._err("Mot de passe actuel incorrect.")

        new_mdp = b.get("newMdp", "")
        if len(new_mdp) < 6:
            return self._err("Au moins 6 caractères requis.")

        user["mdp"] = hash_password(new_mdp)
        _save_data(d)
        self._ok()

    def _gen_reset_code(self, uid: int, b):
        """Génère un code de réinitialisation pour un jeune (admin/éducateur seulement)."""
        requester = get_session(self._token())
        if not requester or requester.get("role") not in ("admin", "educateur"):
            return self._err("Non autorisé.", 403)

        d    = load_data()
        user = next((u for u in d["users"] if u["id"] == uid), None)
        if not user or user["role"] != "jeune":
            return self._err("Utilisateur introuvable ou non jeune.", 404)

        code = "RESET-" + secrets.token_hex(4).upper()
        RESET_TOKENS[uid] = {"code": code, "expires": time.time() + RESET_TTL}
        self._ok({"ok": True, "code": code})

    def _verify_reset(self, b):
        """Vérifie un code de réinitialisation et émet un token à usage unique."""
        login = b.get("login", "").strip().lower()
        code  = b.get("code",  "").strip().upper()

        d    = load_data()
        user = next((u for u in d["users"] if u["login"].lower() == login), None)
        if not user or user["role"] != "jeune":
            return self._err("Identifiant introuvable ou non autorisé.")

        td = RESET_TOKENS.get(user["id"])
        if not td or td["expires"] < time.time():
            return self._err("Code invalide ou expiré.")
        if not hmac.compare_digest(td["code"], code):
            return self._err("Code incorrect.")

        reset_token = secrets.token_urlsafe(32)
        td["reset_token"] = reset_token
        self._ok({
            "ok":         True,
            "userId":     user["id"],
            "resetToken": reset_token,
            "prenom":     user.get("prenom", ""),
        })

    def _reset_password(self, b):
        """Enregistre le nouveau mot de passe via le token à usage unique."""
        uid        = b.get("userId")
        reset_tok  = b.get("resetToken", "")
        new_mdp    = b.get("newMdp",     "")

        if len(new_mdp) < 6:
            return self._err("Au moins 6 caractères requis.")

        td = RESET_TOKENS.get(uid)
        if not td or td.get("reset_token") != reset_tok or td["expires"] < time.time():
            return self._err("Token invalide ou expiré.")

        d    = load_data()
        user = next((u for u in d["users"] if u["id"] == uid), None)
        if not user:
            return self._err("Utilisateur introuvable.")

        user["mdp"] = hash_password(new_mdp)
        _save_data(d)
        RESET_TOKENS.pop(uid, None)
        self._ok()


# ── Lancement ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    httpd = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n{'='*54}")
    print(f"  Objectif+  —  Serveur multi-postes")
    print(f"{'='*54}")
    print(f"  Accès local    :  http://localhost:{PORT}")
    print(f"  Accès réseau   :  http://{local_ip}:{PORT}")
    print(f"  Données        :  {DATA_FILE}")
    print(f"  Arrêter        :  Ctrl+C")
    print(f"{'='*54}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
