# glitch-brain-mcp

Central memory brain for the 6 Glitch Grow AI agents. Multi-brand, multi-agent
shared memory over Postgres, exposed via streamable-HTTP MCP.

- **Port:** 3107 (localhost), fronted by `brain.glitchexecutor.com`
- **DB:** Postgres 17 (shared instance), database `glitch_brain`, extensions
  `pg_trgm` + `vector`
- **Auth:** bearer token per `(brand, agent_sku)` — `agent_sku` may be NULL for
  a brand-wide admin token

## Agents (mirrors `grow-site/src/lib/products.ts`)

| SKU | Name |
|---|---|
| BSK-002 | AI Ads Agent |
| BSK-003 | AI Sales Agent |
| BSK-004 | AI Social Media Agent |
| BSK-005 | Voice AI Agent (LiveKit + Sarvam) |
| BSK-006 | AI SEO Agent |
| BSK-007 | AI UGC Agent |

Brands enable only the agents they pay for. `glitch-executor` is seeded with all 6.

## Scopes

- `agent` — visible only to a single SKU within the brand
- `shared` / `global` — visible to every enabled agent of the brand

Cross-brand reads are never allowed.

## Tools (MCP)

- `remember(content, kind, scope='agent', key?, agent_sku?, metadata?, ttl?)`
- `recall(kind?, key?, agent_sku?, include_shared=true, limit=20)`
- `search(query, agent_sku?, include_shared=true, limit=10)` — trigram similarity
- `forget(memory_id)`

## First-time setup

```bash
# 1. Create role + DB
sudo -u postgres psql <<SQL
CREATE ROLE glitch_brain LOGIN;
CREATE DATABASE glitch_brain OWNER glitch_brain;
SQL

# 2. Schema + seed
sudo -u postgres psql -d glitch_brain -f ops/schema.sql

# 3. Python env
python3 -m venv .venv
.venv/bin/pip install -e .

# 4. Env
cp .env.example .env

# 5. Issue a token for the AI Ads Agent
.venv/bin/glitch-brain-mcp tokens issue \
  --brand glitch-executor --agent BSK-002 --label "ads-agent prod"

# 6. Install systemd + nginx
sudo cp ops/glitch-brain-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now glitch-brain-mcp
sudo cp ops/nginx-brain.glitchexecutor.com.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

After this is up, update `/home/support/glitch-infra/README.md` to add the
service to the inventory.
