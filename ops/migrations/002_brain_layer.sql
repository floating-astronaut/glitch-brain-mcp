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
