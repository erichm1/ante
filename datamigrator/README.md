# Data Migrator

A platform for connecting one or more external systems and migrating records
between them, with a drag-and-drop, UML-style entity/field mapper.

## Stack

- **Backend:** Django, Django REST Framework, Requests
- **Frontend:** Django templates + Bootstrap 5.3, jsPlumb (Community, via CDN) for the mapping canvas
- **Auth:** pluggable per connection — OAuth2 (authorization code, with auto-refresh), API Key / Bearer token, or HTTP Basic
- **Storage:** SQLite by default (swap `DATABASES` in `settings.py` for Postgres in production)

## Setup

```bash
cd datamigrator
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env

# Generate a real key and paste it into .env as FIELD_ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

python manage.py makemigrations connections schemas mappings jobs
python manage.py migrate
python manage.py createsuperuser   # for /admin/, useful for adding fields manually
python manage.py runserver
```

Visit `http://localhost:8000/connections/` to get started.

## How it fits together

1. **Connections** (`connections/`) — register an external system: its base URL
   and how to authenticate to it. Credentials (tokens, API keys, passwords) are
   encrypted at rest with `FIELD_ENCRYPTION_KEY` and never sent back to the
   browser. `connections/client.py` wraps `requests.Session` per connection and
   transparently refreshes OAuth2 tokens when they're close to expiring.

2. **Schemas** (`schemas/`) — each connection has **Entities** (e.g. `Customer`)
   made of **Fields**. You can populate these three ways, all supported:
   - **OpenAPI import** — parses `components.schemas` from a Swagger/OpenAPI document
   - **Live sample** — GETs a real endpoint and infers field names/types from the response
   - **Manual** — create the entity, then add fields from `/admin/`

3. **Mappings** (`mappings/`) — a Mapping pairs a source connection with a
   target connection. Inside it, **Entity Mappings** pair one source entity
   with one target entity, and **Field Mappings** are the individual
   field-to-field wires drawn on the canvas. Open a mapping's canvas, click
   "Add entity pair" to place two entities on the board, then drag from a
   field's teal dot to a field's amber dot to connect them. Click a
   connection to delete it. Entity boxes are draggable and remember their
   position.

4. **Jobs** (`jobs/engine.py`) — running a mapping starts a background thread and
   returns immediately; the canvas polls `GET /api/runs/<id>/` and shows a live
   requests-per-second counter (`requests_made`, updated after every source read
   and every destination write) plus record counts, until the run finishes. Each
   mapped source entity is read via `Requests`, each field's optional transform
   expression is applied (evaluated safely with `simpleeval`, not raw `eval`),
   and the result is posted to the matching destination endpoint. Results and
   per-record errors land in `MigrationRun` / `MigrationLog`, viewable in full
   at `/jobs/runs/<id>/`.

## Field transforms

A `FieldMapping.transform` is a small expression with `value` bound to the
source field's value, e.g. `value.upper()`, `value.strip()`, `value[:10]`,
`round(value, 2)`. Leave it blank to copy the value as-is.

## Known scaffold limitations — next steps for production

- **Migrations run in a plain Python thread**, not a real task queue. Fine for
  a demo and small/medium data sets since progress (including the rq/s
  counter) is genuinely live either way; for production, move
  `jobs/engine.run_migration` into a Celery task instead — the per-record
  logic doesn't change, only who calls it. A thread also dies if the dev
  server restarts mid-run, which a task queue wouldn't.
- **SQLite under concurrent writes** — a run now writes its progress
  (`requests_made`, record counts) from a background thread while the browser
  polls the same row via GET. `OPTIONS: {"timeout": 20}` on the SQLite
  connection absorbs the resulting lock contention for normal use, but a real
  concurrent-load deployment should move to Postgres instead of raising that
  timeout further.
- **No authentication on the app itself** — anyone who can reach it can create
  connections and trigger runs. Add Django auth (`login_required` on the page
  views, an authentication class on the DRF viewsets) before deploying.
- **Pagination isn't applied to source reads** — `run_migration` fetches one
  page from each source entity's `endpoint_path`. For large collections, add
  cursor/page-following in `jobs/engine.py`.
- **One canvas position per entity** — an entity's `canvas_x/canvas_y` is
  shared across every Mapping it appears in, so moving it in one mapping's
  canvas moves it in others too. Fine for a single-mapping-per-entity
  workflow; if that's not your case, move position onto `EntityMapping`
  instead of `Entity`.
- **CSRF on the JSON API** — DRF's `SessionAuthentication` only enforces CSRF
  for *authenticated* sessions, so anonymous fetch() calls from the templates
  work without a token. Once you add login, either send `X-CSRFToken` from
  the JS or switch the API to token/JWT authentication.
