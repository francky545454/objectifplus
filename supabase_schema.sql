-- ============================================================
--  Objectif+ — Schéma Supabase
--  À exécuter dans : Dashboard > SQL Editor > New query
-- ============================================================

-- ─── Données principales (tout dans un seul JSON) ─────────────────────────────
-- users, objectifs, récompenses, échanges, structures
CREATE TABLE IF NOT EXISTS app_data (
    id   TEXT  PRIMARY KEY DEFAULT 'main',
    data JSONB NOT NULL    DEFAULT '{}'::jsonb
);

-- Désactiver RLS (accès géré dans le code Python avec la service_role key)
ALTER TABLE app_data DISABLE ROW LEVEL SECURITY;


-- ─── Codes de réinitialisation de mot de passe ────────────────────────────────
CREATE TABLE IF NOT EXISTS reset_tokens (
    user_id      BIGINT  PRIMARY KEY,
    code         TEXT    NOT NULL,
    reset_token  TEXT,
    expires_at   FLOAT   NOT NULL   -- timestamp Unix (time.time())
);

ALTER TABLE reset_tokens DISABLE ROW LEVEL SECURITY;
