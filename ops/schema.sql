-- glitch_brain schema
-- Run as the glitch_brain DB owner.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS brands (
  brand_id     text PRIMARY KEY,
  display_name text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Catalog mirror of products.ts SKUs.
CREATE TABLE IF NOT EXISTS agents (
  agent_sku  text PRIMARY KEY,   -- BSK-002..BSK-007
  name       text NOT NULL,
  active     boolean NOT NULL DEFAULT true
);

-- Which brand has which agents enabled.
CREATE TABLE IF NOT EXISTS brand_agents (
  brand_id   text NOT NULL REFERENCES brands(brand_id) ON DELETE CASCADE,
  agent_sku  text NOT NULL REFERENCES agents(agent_sku),
  enabled    boolean NOT NULL DEFAULT true,
  PRIMARY KEY (brand_id, agent_sku)
);

-- API tokens. One per brand+agent (agent_sku NULL = brand-wide admin token).
CREATE TABLE IF NOT EXISTS api_tokens (
  token_hash text PRIMARY KEY,         -- sha256 hex of plaintext token
  brand_id   text NOT NULL REFERENCES brands(brand_id) ON DELETE CASCADE,
  agent_sku  text REFERENCES agents(agent_sku),
  label      text,
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);

-- The memories themselves.
-- scope:
--   'global' = visible to every agent of the brand
--   'shared' = visible to every agent of the brand (alias of global for readability)
--   'agent'  = only the named agent_sku
CREATE TABLE IF NOT EXISTS memories (
  id           bigserial PRIMARY KEY,
  brand_id     text NOT NULL REFERENCES brands(brand_id) ON DELETE CASCADE,
  agent_sku    text REFERENCES agents(agent_sku),
  scope        text NOT NULL CHECK (scope IN ('global', 'shared', 'agent')),
  kind         text NOT NULL,           -- e.g. 'fact','lead','preference','event'
  key          text,                    -- optional dedupe key
  content      text NOT NULL,
  metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding    vector(1536),            -- optional, populated later
  ttl          timestamptz,             -- optional expiry
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (brand_id, agent_sku, scope, kind, key)
);

CREATE INDEX IF NOT EXISTS memories_brand_scope_idx
  ON memories (brand_id, scope, agent_sku);
CREATE INDEX IF NOT EXISTS memories_content_trgm_idx
  ON memories USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS memories_metadata_idx
  ON memories USING gin (metadata);

-- Seed catalog from grow site products.ts
INSERT INTO agents (agent_sku, name) VALUES
  ('BSK-002', 'AI Ads Agent'),
  ('BSK-003', 'AI Sales Agent'),
  ('BSK-004', 'AI Social Media Agent'),
  ('BSK-005', 'Voice AI Agent (LiveKit + Sarvam)'),
  ('BSK-006', 'AI SEO Agent'),
  ('BSK-007', 'AI UGC Agent')
ON CONFLICT (agent_sku) DO NOTHING;

-- Seed brands from multi-store-theme-manager/SHOPIFY_STORES_INFRA.md
-- (canonical source for brand families + storefronts).
INSERT INTO brands (brand_id, display_name) VALUES
  ('glitch-executor', 'Glitch Executor'),
  ('urban-classics', 'Urban Classics'),
  ('storico',        'Storico'),
  ('classicoo',      'Classicoo'),
  ('trendsetters',   'Trendsetters'),
  ('ayurpet',        'Ayurpet'),
  ('mokshya',        'Mokshya')
ON CONFLICT DO NOTHING;

-- glitch-executor uses all 6 agents.
INSERT INTO brand_agents (brand_id, agent_sku)
SELECT 'glitch-executor', agent_sku FROM agents
ON CONFLICT DO NOTHING;

-- Urban family + Ayurpet + Mokshya use the Ads agent (BSK-002) and SEO
-- agent (BSK-006). All also use the multi-store Shopify theme manager,
-- which is INTERNAL-ONLY infra (not publicly sold, not one of the 6 AI
-- agents) — lives at shopify.glitchexecutor.com / port 3101.
INSERT INTO brand_agents (brand_id, agent_sku)
SELECT b.brand_id, a.agent_sku
FROM (VALUES ('urban-classics'),('storico'),('classicoo'),
             ('trendsetters'),('ayurpet'),('mokshya')) AS b(brand_id),
     (VALUES ('BSK-002'),('BSK-006')) AS a(agent_sku)
ON CONFLICT DO NOTHING;

-- Ayurpet additionally uses the Social Media agent (BSK-004).
INSERT INTO brand_agents (brand_id, agent_sku) VALUES
  ('ayurpet', 'BSK-004')
ON CONFLICT DO NOTHING;
-- 002_brain_layer.sql — turn shared storage into a brain.
-- Adds: activity log, agent_state, and LISTEN/NOTIFY plumbing.

-- ---------------------------------------------------------------------------
-- Activity: append-only timeline of what each agent did.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity (
  id         bigserial PRIMARY KEY,
  brand_id   text NOT NULL REFERENCES brands(brand_id) ON DELETE CASCADE,
  agent_sku  text NOT NULL REFERENCES agents(agent_sku),
  action     text NOT NULL,            -- e.g. 'campaign.paused', 'page.published'
  subject    text,                     -- the thing acted on: campaign id, url, etc.
  summary    text NOT NULL,            -- short human-readable line
  payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS activity_brand_at_idx ON activity (brand_id, at DESC);
CREATE INDEX IF NOT EXISTS activity_agent_at_idx ON activity (brand_id, agent_sku, at DESC);

-- ---------------------------------------------------------------------------
-- Agent state: current focus / blockers / next, one row per (brand, agent).
-- Sibling agents read this for the "what is everyone up to" view.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_state (
  brand_id      text NOT NULL REFERENCES brands(brand_id) ON DELETE CASCADE,
  agent_sku     text NOT NULL REFERENCES agents(agent_sku),
  current_focus text,
  blockers      text,
  next_step     text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (brand_id, agent_sku)
);

-- ---------------------------------------------------------------------------
-- NOTIFY fan-out. Channel name: brain_<brand_id> (dashes → underscores).
-- Subscribers get JSON {kind, agent_sku, action?, summary?, at, id}.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION brain_notify_activity() RETURNS trigger AS $$
DECLARE
  chan text := 'brain_' || replace(NEW.brand_id, '-', '_');
BEGIN
  PERFORM pg_notify(chan, json_build_object(
    'kind', 'activity',
    'id', NEW.id,
    'agent_sku', NEW.agent_sku,
    'action', NEW.action,
    'subject', NEW.subject,
    'summary', NEW.summary,
    'at', NEW.at
  )::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS activity_notify ON activity;
CREATE TRIGGER activity_notify AFTER INSERT ON activity
  FOR EACH ROW EXECUTE FUNCTION brain_notify_activity();

CREATE OR REPLACE FUNCTION brain_notify_state() RETURNS trigger AS $$
DECLARE
  chan text := 'brain_' || replace(NEW.brand_id, '-', '_');
BEGIN
  PERFORM pg_notify(chan, json_build_object(
    'kind', 'state',
    'agent_sku', NEW.agent_sku,
    'current_focus', NEW.current_focus,
    'blockers', NEW.blockers,
    'next_step', NEW.next_step,
    'at', NEW.updated_at
  )::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_state_notify ON agent_state;
CREATE TRIGGER agent_state_notify AFTER INSERT OR UPDATE ON agent_state
  FOR EACH ROW EXECUTE FUNCTION brain_notify_state();
