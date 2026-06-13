# CLAUDE.md — Objectif+

Ce fichier donne le contexte technique à Claude Code pour travailler efficacement sur ce projet.

---

## Vue d'ensemble

**Objectif+** est une PWA (Progressive Web App) de suivi d'objectifs éducatifs pour adolescents.  
Déployée sur Vercel, base de données Supabase.

| URL production | `https://objectifplus.vercel.app` |
|---|---|
| Repo GitHub | `https://github.com/francky545454/objectifplus` |
| Déploiement | Push sur `main` → Vercel auto-déploie |
| Base de données | Supabase (table `app_data`, 1 blob JSON) |

---

## Stack technique

| Composant | Technologie |
|---|---|
| Backend | Python / Flask (`app.py`) |
| Frontend | Single-page HTML (`V5_objectifplus.html`) avec React + Babel CDN |
| Auth | PBKDF2-HMAC-SHA256 custom + tokens HMAC-signés (pas de lib JWT) |
| BDD | Supabase — table `app_data` colonne `data` (JSONB), 1 seule ligne |
| Emails | Resend (`RESEND_API_KEY`, `NOTIF_EMAIL`) |
| Hébergement | Vercel (serverless Python, `@vercel/python`) |
| PWA | `manifest.json`, `sw.js` (stratégie network-first) |

---

## Fichiers clés

| Fichier | Rôle |
|---|---|
| `app.py` | Entrée Vercel serverless — toutes les routes API + sert le HTML |
| `server.py` | Serveur local de dev (port 5001), même logique que `app.py` |
| `V5_objectifplus.html` | **Frontend complet** — toute la logique React est ici |
| `vercel.json` | Config Vercel : builder Python, inclut HTML + PWA assets, **cron ping** |
| `sw.js` | Service Worker — network-first, cache `objp-v5` |
| `supabase_schema.sql` | Schéma SQL de la table `app_data` |
| `requirements.txt` | Dépendances Python (`flask`, `supabase`) |
| `backup_auto.py` | Script de sauvegarde automatique quotidienne (23h00) |
| `backup_config.json` | Config sauvegarde (identifiants admin — **ne pas versionner**) |
| `lancer_sauvegarde.bat` | Lanceur manuel ou via Planificateur de tâches Windows |

---

## Structure des données (Supabase)

Tout est stocké dans **1 ligne** de la table `app_data`, colonne `data` (JSONB) :

```json
{
  "users":       [...],   // admin, éducateurs, jeunes
  "structures":  [...],   // organismes / structures
  "objectifs":   [...],   // objectifs des jeunes
  "recompenses": [...],   // système de récompenses
  "echanges":    [...],   // échanges de points
  "tickets":     [...]    // tickets de support éducateur → admin
}
```

**Auto-save** : debounce 800 ms dans le composant React `App` → `POST /api/data`.

---

## Profils utilisateurs

| Rôle | Droits |
|---|---|
| `superadmin` | Accès total, gestion des admins |
| `admin` | Gestion structures, éducateurs, jeunes, stats globales |
| `educateur` | Gestion des jeunes de sa structure, création objectifs |
| `jeune` | Vue de ses propres objectifs et récompenses |

Champ clé : `user.structureId` (Number) — lie un utilisateur à une structure.

---

## Points techniques importants

### 1. Normalisation des IDs (bug critique corrigé)
Les IDs stockés dans Supabase peuvent être des strings (`"1749478..."`) alors que JS crée des numbers (`Date.now()`). **Toujours normaliser avec `Number()`** au chargement :

```javascript
// Dans le useEffect de chargement des données (App component)
var normStructures = (d.structures||[]).map(s => ({...s, id: Number(s.id)}));
var normUsers = (d.users||[]).map(u => ({...u, id: Number(u.id), structureId: Number(u.structureId)||null}));
```

### 2. Auto-affectation des orphelins
Si `user.structureId` ne correspond à aucune structure ET qu'il n'existe qu'une seule structure → auto-affecter à cette structure (cas de suppression/recréation de structure).

### 3. Déploiement — NE PAS utiliser `vercel deploy`
Le CLI Vercel est lié à l'équipe `francky5454s-projects`, **pas** au compte qui héberge `objectifplus.vercel.app`.  
**La seule méthode valide : `git push origin main`** → déclenche le déploiement Vercel automatiquement.

### 4. React dans le HTML
Tout le frontend est en JSX transpilé in-browser par Babel CDN. Les composants ne sont **pas** dans des fichiers séparés — tout est dans `V5_objectifplus.html`. Chercher les sections avec `// ─── NomComposant` dans le fichier.

### 5. Session persistante (localStorage)
Le token d'auth est stocké dans `localStorage` (pas `sessionStorage`). La fonction `decodeStoredToken()` le relit au démarrage pour éviter de redemander la connexion à chaque F5. Token valide 8h ; déconnexion propre si expiré.

### 6. Suppression d'utilisateur — endpoint dédié
La suppression passe par `DELETE /api/users/<id>` (superadmin uniquement).  
**Ne pas** envoyer la liste filtrée via `POST /api/data` — le backend réinjecterait les utilisateurs manquants (comportement de merge voulu pour les mises à jour partielles).

---

## Maintien en vie de Supabase (anti-pause)

Supabase free tier met les projets en pause après **7 jours sans activité**.  
Deux mécanismes actifs pour éviter ça :

| Mécanisme | Fréquence | Déclencheur |
|---|---|---|
| **Cron Vercel** `GET /api/ping` | Tous les jours à **6h00 UTC** | Vercel (automatique, côté serveur) |
| **Sauvegarde Windows** `backup_auto.py` | Tous les jours à **23h00** | Planificateur de tâches Windows |

Le cron Vercel est le plus fiable (ne dépend pas que le PC soit allumé).  
La sauvegarde Windows est le filet de sécurité secondaire + crée une copie locale des données.

```
vercel.json → "crons": [{ "path": "/api/ping", "schedule": "0 6 * * *" }]
app.py      → GET /api/ping  (lecture légère Supabase, pas d'auth requise)
```

---

## Sauvegarde automatique locale

Script : `backup_auto.py`  
Planificateur Windows : tâche *"ObjectifPlus - Sauvegarde automatique"* — 23h00 chaque soir  
Fichier produit : `sauvegarde_objectifplus.json` (écrasé à chaque exécution — 1 seul fichier)  
Log : `backup.log` (200 dernières lignes)

Config (`backup_config.json`, **gitignorée**) :
```json
{ "api_url": "https://objectifplus.vercel.app", "login": "...", "password": "..." }
```

Pour tester manuellement : double-clic sur `lancer_sauvegarde.bat`

---

## Démarrage local

```bat
cd "C:\Users\franc\LOGICIELS\Objectif+"
venv\Scripts\python server.py
```
Ouvre `http://127.0.0.1:5001`

Premier lancement :
```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Variables d'environnement (fichier `.env` ou variables système) :
```
SUPABASE_URL=
SUPABASE_KEY=      # service_role key
OBJP_SECRET=       # chaîne aléatoire ≥ 32 chars
RESEND_API_KEY=
NOTIF_EMAIL=
```

Sans Supabase, l'app tourne en mode dégradé (`sb = None`) avec `data/app_data.json` local.

---

## Routes API principales

| Méthode | Route | Description |
|---|---|---|
| GET | `/` | Sert `V5_objectifplus.html` |
| GET | `/api/ping` | Health check + lecture Supabase (cron anti-pause) |
| POST | `/api/auth` | Authentification, retourne un token |
| GET | `/api/data` | Charge toutes les données (authentifié) |
| POST | `/api/data` | Sauvegarde toutes les données (authentifié) |
| POST | `/api/users` | Crée un utilisateur (admin only) |
| DELETE | `/api/users/<id>` | Supprime un utilisateur (superadmin only) |
| POST | `/api/users/<id>/set-password` | Change le mot de passe |
| POST | `/api/tickets` | Crée un ticket de support |

---

## CPS — Compétences PsychoSociales

Les objectifs ont un champ `cps` (string) qui référence une compétence psychosociale.  
Utilisé dans l'onglet Stats pour mettre en exergue la CPS la plus et la moins réussie.  
Exemples : `"Confiance en soi"`, `"Gestion des émotions"`, `"Communication"`, etc.

---

## Historique des versions frontend

| Fichier | Notes |
|---|---|
| `V2 - avec CPS.html` | Prototype avec CPS |
| `V3_objectifplus.html` | Version intermédiaire |
| `V4_objectifplus.html` | Pré-production |
| `V5_objectifplus.html` | **Version active** |
