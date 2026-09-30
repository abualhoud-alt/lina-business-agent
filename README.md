# Lina V6 — Production Foundation

This package upgrades the V5 backend into a deployable single-service production foundation for Matrix Business Services.

## Included
- FastAPI backend + mobile-responsive MBS Lina interface
- Persistent SQLite database under configurable `LINA_DATA_DIR`
- Companies, contacts, opportunities, activities, quotations, invoices
- Margin/VAT calculations
- Owner-only quotation approval gate
- Audit log
- API-key authentication (`X-API-Key`)
- Security headers
- Health check
- Docker deployment
- Render Blueprint (`render.yaml`) with persistent disk
- No demo pipeline data

## Recommended first deployment
Render can deploy a Docker service and supports persistent disks; the Blueprint is included for this path. The current database is intentionally SQLite for the first live release. Before high-volume/multi-user operation, migrate the persistence layer to managed PostgreSQL.

## Required secret
Set `LINA_API_KEY` to a long random secret in the hosting provider. Never commit it to Git.

## Local run
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
set LINA_AUTH_DISABLED=true   # Windows cmd
# or: export LINA_AUTH_DISABLED=true
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/

## Important production boundary
This is deployable infrastructure, but live email, calendar, CRM, web lead research, document storage, payment systems, and outbound sending still require authenticated connections and explicit owner permissions. Lina's approval rule remains: prepare/recommend -> Khodor approves -> execute.
