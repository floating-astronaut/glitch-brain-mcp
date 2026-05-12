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

-- Only glitch-executor uses all 6 agents today. Other brands start with
-- nothing enabled; flip on per-agent as subscriptions are sold.
INSERT INTO brand_agents (brand_id, agent_sku)
SELECT 'glitch-executor', agent_sku FROM agents
ON CONFLICT DO NOTHING;
