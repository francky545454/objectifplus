"""
Objectif+ — Version Vercel / Supabase
Identique à server.py mais adapté pour le déploiement serverless.

Variables d'environnement requises (Vercel > Settings > Environment Variables) :
  SUPABASE_URL   — URL du projet Supabase
  SUPABASE_KEY   — service_role key (pas la anon key)
  OBJP_SECRET    — chaîne aléatoire secrète pour signer les tokens (≥ 32 chars)
"""

import os, json, hashlib, hmac, secrets, time, base64, urllib.request, urllib.error
from flask import Flask, request, jsonify, send_from_directory, make_response

# ── Supabase ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SECRET       = os.environ.get("OBJP_SECRET", "dev-secret-change-me")
RESEND_KEY   = os.environ.get("RESEND_API_KEY", "")
NOTIF_EMAIL  = os.environ.get("NOTIF_EMAIL", "")
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))

if SUPABASE_URL and SUPABASE_KEY:
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    sb = None  # mode développement local sans Supabase

app = Flask(__name__)

RESET_TTL = 30 * 60  # 30 minutes


# ── Mots de passe ──────────────────────────────────────────────────────────────

def hash_password(pwd: str) -> str:
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


# ── Tokens HMAC (stateless, pas de stockage serveur) ──────────────────────────

def make_token(user_safe: dict, hours: int = 8) -> str:
    payload = {"u": user_safe, "exp": time.time() + hours * 3600}
    data    = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig     = hmac.new(SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{data}.{sig}"

def decode_token(token: str):
    if not token:
        return None
    try:
        data, sig = token.rsplit(".", 1)
        expected  = hmac.new(SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            return None
        padding = "=" * (-len(data) % 4)
        payload = json.loads(base64.urlsafe_b64decode(data + padding))
        if payload["exp"] < time.time():
            return None
        return payload["u"]
    except Exception:
        return None

def get_requester():
    auth  = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    return decode_token(token)


# ── Persistance Supabase ───────────────────────────────────────────────────────

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
    if not sb:
        return _default_data()
    res = sb.table("app_data").select("data").eq("id", "main").execute()
    if res.data:
        return res.data[0]["data"]
    data = _default_data()
    sb.table("app_data").insert({"id": "main", "data": data}).execute()
    return data

def save_data(data: dict):
    if not sb:
        return
    sb.table("app_data").upsert({"id": "main", "data": data}).execute()

def strip_passwords(users: list) -> list:
    return [{k: v for k, v in u.items() if k != "mdp"} for u in users]


# ── Codes de réinitialisation (Supabase) ──────────────────────────────────────

def _get_reset(user_id):
    if not sb:
        return None
    res = sb.table("reset_tokens").select("*").eq("user_id", user_id).execute()
    if not res.data:
        return None
    row = res.data[0]
    if row["expires_at"] < time.time():
        sb.table("reset_tokens").delete().eq("user_id", user_id).execute()
        return None
    return row

def _set_reset(user_id, code):
    if not sb:
        return
    sb.table("reset_tokens").upsert({
        "user_id":     user_id,
        "code":        code,
        "reset_token": None,
        "expires_at":  time.time() + RESET_TTL
    }).execute()

def _update_reset_token(user_id, reset_token):
    if not sb:
        return
    sb.table("reset_tokens").update({"reset_token": reset_token}).eq("user_id", user_id).execute()

def _delete_reset(user_id):
    if not sb:
        return
    sb.table("reset_tokens").delete().eq("user_id", user_id).execute()


# ── Helpers réponses ───────────────────────────────────────────────────────────

def ok(data=None, status=200):
    return jsonify(data or {"ok": True}), status

def err(msg, status=400):
    return jsonify({"ok": False, "error": msg}), status


# ── Routes statiques ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "V5_objectifplus.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(BASE_DIR, "manifest.json", mimetype="application/json")

@app.route("/sw.js")
def service_worker():
    resp = make_response(send_from_directory(BASE_DIR, "sw.js"))
    resp.headers["Content-Type"] = "application/javascript"
    return resp

@app.route("/icons/<path:filename>")
def icons(filename):
    mime = "image/svg+xml" if filename.endswith(".svg") else "image/png"
    return send_from_directory(os.path.join(BASE_DIR, "icons"), filename, mimetype=mime)


# ── API : données ──────────────────────────────────────────────────────────────

@app.route("/api/data", methods=["GET"])
def api_data_get():
    d = load_data()
    return ok({
        "users":       strip_passwords(d["users"]),
        "objectifs":   d["objectifs"],
        "recompenses": d["recompenses"],
        "echanges":    d["echanges"],
        "structures":  d["structures"],
    })

@app.route("/api/data", methods=["POST"])
def api_data_post():
    b = request.get_json(force=True) or {}
    d = load_data()

    if "users" in b:
        existing  = {u["id"]: u for u in d["users"]}
        merged    = []
        seen_ids  = set()
        for u in b["users"]:
            uid = u.get("id")
            ex  = existing.get(uid)
            if ex:
                nu = dict(ex)
                for k, v in u.items():
                    if k != "mdp":
                        nu[k] = v
                merged.append(nu)
                seen_ids.add(uid)
        for uid, eu in existing.items():
            if uid not in seen_ids:
                merged.append(eu)
        d["users"] = merged

    for k in ("objectifs", "recompenses", "echanges", "structures"):
        if k in b:
            d[k] = b[k]

    save_data(d)
    return ok()


# ── API : authentification ─────────────────────────────────────────────────────

@app.route("/api/auth", methods=["POST"])
def api_auth():
    b     = request.get_json(force=True) or {}
    login = b.get("login", "").strip().lower()
    mdp   = b.get("mdp",   "")
    d     = load_data()
    user  = next((u for u in d["users"] if u["login"].lower() == login), None)
    if user and check_password(mdp, user.get("mdp", "")):
        safe  = {k: v for k, v in user.items() if k != "mdp"}
        token = make_token(safe)
        return ok({"ok": True, "user": safe, "token": token})
    return err("Identifiant ou mot de passe incorrect.", 401)


# ── Notifications email (Resend) ──────────────────────────────────────────────

def send_ticket_email(ticket, edu_name):
    """Envoie un email de notification à l'admin quand un nouveau ticket arrive."""
    if not RESEND_KEY or not NOTIF_EMAIL:
        return
    priorite_label = "🔴 URGENT" if ticket.get("priorite") == "urgente" else "🔵 Normale"
    subject = f"[Objectif+] {'🔴 URGENT — ' if ticket.get('priorite')=='urgente' else ''}Nouveau ticket : {ticket['titre']}"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
  <div style="background:#7c3aed;padding:16px 20px;border-radius:10px 10px 0 0">
    <h2 style="color:#fff;margin:0">🎫 Nouveau ticket — Objectif+</h2>
  </div>
  <div style="background:#f9f9f9;padding:20px;border:1px solid #e5e7eb;border-radius:0 0 10px 10px">
    <p><strong>De :</strong> {edu_name}</p>
    <p><strong>Titre :</strong> {ticket['titre']}</p>
    <p><strong>Priorité :</strong> {priorite_label}</p>
    <p><strong>Message :</strong></p>
    <blockquote style="background:#fff;border-left:4px solid #7c3aed;padding:10px 16px;margin:8px 0;border-radius:4px;color:#374151">
      {ticket['message'].replace(chr(10), '<br>')}
    </blockquote>
    <a href="https://objectifplus.vercel.app" style="display:inline-block;margin-top:16px;background:#7c3aed;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:bold">
      Ouvrir Objectif+
    </a>
  </div>
</div>"""
    try:
        payload = json.dumps({
            "from": "Objectif+ <onboarding@resend.dev>",
            "to": [NOTIF_EMAIL],
            "subject": subject,
            "html": html
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass  # Ne pas bloquer si l'email échoue


# ── API : tickets ──────────────────────────────────────────────────────────────

@app.route("/api/tickets", methods=["POST"])
def api_create_ticket():
    requester = get_requester()
    if not requester or requester.get("role") not in ("superadmin", "admin", "educateur"):
        return err("Non autorisé.", 403)

    b = request.get_json(force=True) or {}
    titre   = b.get("titre",   "").strip()
    message = b.get("message", "").strip()
    if not titre or not message:
        return err("Titre et message obligatoires.")

    ticket = {
        "id":           int(time.time() * 1000),
        "educateurId":  requester["id"],
        "titre":        titre,
        "message":      message,
        "priorite":     b.get("priorite", "normale"),
        "statut":       "ouvert",
        "date":         time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reponse":      None,
        "reponseDate":  None,
    }

    d = load_data()
    d.setdefault("tickets", []).append(ticket)
    save_data(d)

    edu_name = f"{requester.get('prenom','')} {requester.get('nom','')}".strip() or requester.get("login", "")
    send_ticket_email(ticket, edu_name)

    return ok({"ok": True, "ticket": ticket})


# ── API : utilisateurs ─────────────────────────────────────────────────────────

@app.route("/api/users", methods=["POST"])
def api_create_user():
    requester = get_requester()
    if not requester or requester.get("role") not in ("superadmin", "admin", "educateur"):
        return err("Non autorisé.", 403)

    b = request.get_json(force=True) or {}
    if requester["role"] == "educateur" and b.get("role") != "jeune":
        return err("Les éducateurs peuvent uniquement créer des comptes jeunes.", 403)

    login = b.get("login", "").strip().lower()
    mdp   = b.get("mdp",   "")
    if not login or not mdp:
        return err("Identifiant et mot de passe obligatoires.")

    d = load_data()
    if any(u["login"].lower() == login for u in d["users"]):
        return err("Cet identifiant existe déjà.")

    nu = {
        "id":          int(time.time() * 1000),
        "login":       login,
        "mdp":         hash_password(mdp),
        "role":        b.get("role",        "jeune"),
        "prenom":      b.get("prenom",      ""),
        "nom":         b.get("nom",         ""),
        "age":         b.get("age")         or None,
        "avatar":      b.get("avatar",      "👤"),
        "couleur":     b.get("couleur",     "#7c3aed"),
        "structureId": b.get("structureId") or None,
    }
    d["users"].append(nu)
    save_data(d)
    safe = {k: v for k, v in nu.items() if k != "mdp"}
    return ok({"ok": True, "user": safe})


@app.route("/api/users/<int:uid>/set-password", methods=["POST"])
def api_set_password(uid):
    requester = get_requester()
    if not requester or requester.get("role") not in ("superadmin", "admin", "educateur"):
        return err("Non autorisé.", 403)

    b       = request.get_json(force=True) or {}
    new_mdp = b.get("newMdp", "")
    if len(new_mdp) < 6:
        return err("Au moins 6 caractères requis.")

    d    = load_data()
    user = next((u for u in d["users"] if u["id"] == uid), None)
    if not user:
        return err("Utilisateur introuvable.", 404)
    if requester["role"] == "educateur" and user["role"] != "jeune":
        return err("Les éducateurs ne peuvent réinitialiser que le mot de passe des jeunes.", 403)

    user["mdp"] = hash_password(new_mdp)
    save_data(d)
    return ok()


@app.route("/api/users/<int:uid>/gen-reset-code", methods=["POST"])
def api_gen_reset_code(uid):
    requester = get_requester()
    if not requester or requester.get("role") not in ("superadmin", "admin", "educateur"):
        return err("Non autorisé.", 403)

    d    = load_data()
    user = next((u for u in d["users"] if u["id"] == uid), None)
    if not user or user["role"] != "jeune":
        return err("Utilisateur introuvable ou non jeune.", 404)

    code = "RESET-" + secrets.token_hex(4).upper()
    _set_reset(uid, code)
    return ok({"ok": True, "code": code})


# ── API : mots de passe ────────────────────────────────────────────────────────

@app.route("/api/change-password", methods=["POST"])
def api_change_password():
    requester = get_requester()
    if not requester:
        return err("Non authentifié.", 401)

    b = request.get_json(force=True) or {}
    d = load_data()
    user = next((u for u in d["users"] if u["id"] == requester["id"]), None)
    if not user or not check_password(b.get("currentMdp", ""), user.get("mdp", "")):
        return err("Mot de passe actuel incorrect.")

    new_mdp = b.get("newMdp", "")
    if len(new_mdp) < 6:
        return err("Au moins 6 caractères requis.")

    user["mdp"] = hash_password(new_mdp)
    save_data(d)
    return ok()


@app.route("/api/verify-reset-code", methods=["POST"])
def api_verify_reset():
    b     = request.get_json(force=True) or {}
    login = b.get("login", "").strip().lower()
    code  = b.get("code",  "").strip().upper()

    d    = load_data()
    user = next((u for u in d["users"] if u["login"].lower() == login), None)
    if not user or user["role"] != "jeune":
        return err("Identifiant introuvable ou non autorisé.")

    td = _get_reset(user["id"])
    if not td:
        return err("Code invalide ou expiré.")
    if not hmac.compare_digest(td["code"], code):
        return err("Code incorrect.")

    reset_token = secrets.token_urlsafe(32)
    _update_reset_token(user["id"], reset_token)
    return ok({"ok": True, "userId": user["id"], "resetToken": reset_token, "prenom": user.get("prenom", "")})


@app.route("/api/reset-password", methods=["POST"])
def api_reset_password():
    b          = request.get_json(force=True) or {}
    uid        = b.get("userId")
    reset_tok  = b.get("resetToken", "")
    new_mdp    = b.get("newMdp",     "")

    if len(new_mdp) < 6:
        return err("Au moins 6 caractères requis.")

    td = _get_reset(uid)
    if not td or td.get("reset_token") != reset_tok:
        return err("Token invalide ou expiré.")

    d    = load_data()
    user = next((u for u in d["users"] if u["id"] == uid), None)
    if not user:
        return err("Utilisateur introuvable.")

    user["mdp"] = hash_password(new_mdp)
    save_data(d)
    _delete_reset(uid)
    return ok()


# ── Lancement local ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5001)
