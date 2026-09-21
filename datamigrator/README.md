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

5. **Studio** (`studio/`, `static/js/studio/`) — one Pentaho-Spoon-style workspace
   at `/studio/` that replaces the separate Mappings / Canvas / Runs / Plans /
   Chains pages in the navbar:

   - **Explorer** (left, *View* tab): every mapping, chain, plan, your recent
     runs and connections. Click one to open it in a tab; `+` creates a new one.
     The *Design* tab is a context-sensitive palette for the open canvas.
   - **Chain step kinds.** A chain step is no longer only an HTTP call. The Design tab offers
     *Requests* (GET … DELETE, each with an optional **timeout**), *Flow* — **Wait** (pause, capped
     at 300 s), **Async job** (start a job, poll until done) and **Next page** (repeat a GET
     following the response's next link or cursor, collecting every page's items) — *Data* —
     **Find in list** (search a list an earlier step produced for the item whose field
     equals/contains/matches…, then capture from it) and **File preview** (read the first rows of
     an uploaded CSV/XLSX, with a live preview table in the dialog) — and *Checks* — **Check header**
     (assert an earlier response's header exists / equals / matches, stopping the chain or just
     warning). Every kind can capture values for later steps, HTTP results keep their response
     headers, and each kind's result is described and expandable in the results panel. A
     next-page link to a different host is never followed (the connection's credentials would go
     with it). All kinds run through one runner in `chains/executor.py`, retries included.
   - **Desktop only.** The Studio is a canvas editor, so below 768 px wide it shows a notice
     asking you to use the desktop (web) application and loads nothing; the layout CSS is
     mobile-first (the workspace is `display: none` by default and only appears from 768 px up,
     and starts itself if a window is widened past that). Everything else in Ante is meant to work
     on a phone — page headers wrap their action buttons, wide tables scroll inside their panel.
   - **Canvas controls** (also under the **?** button in every canvas's corner): scroll pans,
     **Ctrl/⌘ + scroll** (or a trackpad pinch) zooms, Shift + scroll pans sideways, dragging empty
     space pans, dragging a block moves it, right-click opens its menu. Each canvas adds its own
     section — how to wire fields, add steps, or build a run.
   - **Custom run (drag & drop):** *New → Custom run…* opens a blank plan with the Design tab in
     front. Drag onto the canvas a **mapping**, **one of its entity pairs** (expand a mapping in
     the palette with › to run just that pair), a **chain**, or a **Wait** (pause up to an hour).
     Drop on a step to insert before it, on empty space to add at the end; drag a step onto another
     to reorder (the target lights up). Then *Execute* — or schedule it. Plans are editable until
     they are executing/scheduled, so a finished run can be reshaped and run again; each execution
     starts from a clean slate. (`POST /api/plans/<id>/steps/` takes `wait_seconds`,
     `entity_mapping_ids` and `position`; `POST /api/plans/<id>/reorder/` sets the whole order.)
   - **Function blocks in a custom run.** Besides mappings, pairs, chains and Wait, the run's Design tab
     offers every step type as a drag-and-drop block: **requests** (GET…DELETE, each naming the
     connection it calls and with an optional timeout), **Async job**, **Next page**, **Find in list**,
     **File preview** and **Check header**. They live in one hidden per-plan chain and run on a single shared
     context in plan order, so a *find* can search what an earlier request returned, a detail request can use
     the id it found, and a *check* can read an earlier response's headers — even with a mapping run in
     between. A failed block stops the run. (`POST /api/plans/<id>/steps/` takes `function: {kind, name, …,
     connection}`; the hidden chain is never listed as a chain of its own and is deleted with the plan.)
   - **Headers and query parameters on every request.** A request step — in a chain or as a function block in
     a custom run — takes any number of extra **headers** (with suggestions for the common ones) and **query
     parameters**; values may use `{{placeholders}}` from earlier steps, exactly like the path and body. They are
     merged over the connection's own auth headers, secrets are masked in the recorded request, and a block on the
     canvas shows how many it sends. (`headers` / `query_params`: `[{name, value}, …]` on chain steps and on a plan's
     `function`.)
   - **Kill.** The toolbar's red **Kill** button stops whatever the open document is running — a mapping's
     migration, a chain, or a whole plan (or cancels a scheduled plan) — and per-run *Kill/Cancel* buttons sit in a
     mapping's Runs table. Stopping is cooperative: a migration stops between records, a chain between steps
     and mid-wait/poll/page, a plan before its next step (and mid-wait) *and* it tells the step in progress to
     stop. Records already written stay written, and a request already on the wire finishes or times out
     first. Killed runs end as **cancelled** (not failed), can be retried/resumed, and a run whose thread
     died (a server restart) is closed immediately by a Kill instead of hanging as "running" forever.
     (`POST /api/runs/<id>/cancel/`, `/api/mappings/<id>/cancel/`, `/api/chains/<id>/cancel/`,
     `/api/plans/<id>/cancel/`.) Scaffold caveat: the "is this run alive?" registry is per-process.
   - **Single run:** *New → Single run…* runs one mapping once without opening it first — pick
     the mapping, optionally schedule it, cap its rate or attach input file(s) — then opens the
     mapping on that run.
   - **Templates:** the first explorer section (and *New → From a template…*) lists starter
     recipes; pick one, answer a short form, and it builds an ordinary mapping / chain / plan
     you can then edit. *Copy matching fields*, *Import a CSV / Excel file* and *Sync one
     source to several destinations* create the pairs and wire every field whose name
     matches (ignoring case, `_`/`-`, and a dotted prefix like `precos.preco`), optionally
     adding a trim / UPPER / lower transform to text→text wires, and report what it couldn't
     match. *Create, then link*, *List, then fetch one* and *Start a job and wait for it*
     build chains (with captures and async polling pre-filled); *Ordered plan* sequences
     existing mappings and chains; *Run a migration now* / *Schedule a migration* open a
     mapping's run dialog. Templates live in `studio/templates.py` (catalog + appliers, all
     atomic) and are served at `/studio/templates/`.
   - **Logos:** every row in the explorer — and the tab of any open document — wears the
     App Store icons of the systems it touches: a mapping shows origin → destination(s), a
     chain its connection, a plan each system its steps use, a connection its own. An
     uploaded integration logo is used when there is one, else its icon class.
   - **Edit / delete from anywhere:** hover a row for ✎ / 🗑, right-click a row or a tab, or
     use a step on the canvas — a plan step's ✎ / 🗑 act on the mapping or chain it runs
     (right-click for the full menu), and a chain's or plan's START node edits/deletes the
     chain or plan itself. Deleting says what goes with it, including how many plan steps
     use it.
   - **Canvas** (centre, one tab per document): a *mapping* is the field-wiring
     canvas (drag a teal dot onto an amber one; click a wire for its transform);
     a *chain* is a flow of HTTP steps joined by hops; a *plan* is a job —
     START → mapping/chain entries → DONE — where double-clicking an entry opens
     what it runs. Pan by dragging, zoom with the wheel or the toolbar. Tabs can be
     dragged into any order (or right-clicked: move, close, close others); the
     order is remembered.
   - **Results** (bottom, follows the open tab): a mapping's field mappings, run
     history, per-entity step metrics and live log; a chain's runs, step
     request/response and captured variables; a plan's per-step status. Entities
     and steps wear a tick/cross after a run, live while it executes.
   - **Data in, data out, sync health.** A mapping's *Data preview* tab is a
     read-only dry run (`GET /api/mappings/<id>/preview/`): a sample of the source
     rows beside the exact payloads the field mappings would send, with unmapped
     columns dimmed and transformed cells flagged — nothing is written (it shares
     `jobs.engine.build_payload` with real runs, so it can't drift from them).
     During and after a run the canvas shows what flowed through each entity
     (read / written / failed) under its box, and wires of a running pair carry
     moving dashes; *Step metrics* adds a live throughput sparkline and per-pair
     progress meters; the *Runs* tab opens with a sync-health dashboard (last
     sync, success rate, records written, average duration, one stacked column per
     run — click one to inspect it). Chains get the same history chart plus a
     "most failing step" tile; plans show a records meter overall and per step.
     Chart colours are validated (`dataviz` palette checks) for colour-blind
     separation and contrast in both themes.
   - **Toolbar**: Run (a migration, a chain, or a whole plan), Options
     (schedule / rate limit / input files), Properties, Delete.

   Everything goes through the same REST API as before — the only Studio-only
   endpoint is `GET /studio/tree/` (explorer data). Plans created in the Studio
   use `execution_mode = "mixed"`, so one plan can interleave mapping runs and
   chain executions; `plans/executor.py` dispatches on each step's own target.
   The old pages still work at their URLs (run detail/snapshot pages are what the
   Studio's *Detail* / *Pipeline* links open); a mixed plan's old detail page
   redirects into the Studio. `/api/runs/?slim=1` lists runs without their logs.

## Users, groups, departments and permissions

The app is split into **modules** — Status, App Store & connections, Mappings, Jobs (runs), Plans, Chains,
Studio, Reports, Tickets, Incidents and Logs — and each one is simply **on or off** for a person
(`accounts/permissions.py` lists them and which URL prefixes each unlocks).

- A user's access = *(the modules of all their groups + modules switched **On** for them) − modules switched **Off**
  for them*. **Off always wins.** Administrators (`is_staff` / superuser) have every module, always.
- **Groups** (`AccessGroup`) are named sets of modules. **Departments** are organisational labels only. A built-in
  default group, **Standard user**, holds everything an ordinary user could reach before permissions existed (all
  modules except Incidents) and is given to every existing and new user, so nobody is locked out by an upgrade —
  switch it off per user or edit the group to tighten things. Any group can be marked *default for new users*.
- **Enforcement** is in `accounts/middleware.py::ModuleAccessMiddleware`, so it covers pages *and* the JSON API:
  a page the user may not use redirects to Home with a **warning message** ("You don't have permission to use
  Mappings. Ask an administrator to enable it for your account."); `/api/…`, `/studio/tree/` and
  `/studio/templates/` answer `403 {"error", "permission_denied": true, "modules": […]}`, which the Studio shows as
  a warning toast (not an error). The Studio lists only what the user may see, locks the sections and New-menu
  entries it can't offer, and templates are filtered the same way.
- **Navbar:** links for Mappings, Runs (`/jobs/`), Plans and Chains next to the Studio; a module the user lacks stays
  visible with a 🔒 (clicking it explains why) and the Django-admin link is for staff only. The company logo
  (Profile → Company logo) shows beside the avatar.
- **Administration** — on the **Profile** page, administrators get Users / Groups / Departments tabs: create, edit,
  delete and filter; per user a department, groups, active / administrator / superuser flags, an optional new
  password and every module as **Inherit / On / Off** with a live "effective access" readout. Behind it is
  `/api/admin-users/`, `/api/admin-groups/`, `/api/admin-departments/`, `/api/admin-modules/` (administrators only).
  Guard rails: you can't deactivate, demote or delete yourself, nor the last active administrator; only a
  superuser can grant/revoke superuser or touch a superuser's account; passwords go through Django's validators.
- Incidents used to be hard-wired to `is_staff`; it is now the ordinary *Incidents* module (off in the default group).

## Notifications

Every migration run, chain run, plan and scheduled job that ends produces an in-app notification —
**success**, **failed**, **cancelled** (called off before it started: a queued run, a scheduled plan) or **killed**
(stopped while running with the Kill button, or found orphaned and closed). Each one names what happened, gives the
counts (or the step that broke) and links to the run.

- Recipients: every active user who has the relevant module (Jobs / Chains / Plans) — runs have no owner, so the
  audience is whoever may see them. A run or chain run that belongs to a plan is *not* announced separately (the
  plan's notification covers it), and a plan's hidden function-block chain is silent.
- The **bell** in the navbar shows the unread count and the latest few (polled every 30 s while the tab is
  visible; on a phone it links straight to the list); `/notifications/` has the full history with filters
  (outcome, type, unread only). Opening one marks it read; *Mark all read* clears the badge.
- Creating notifications is best-effort (`notifications/services.py`): a failure there is logged and never breaks
  or delays the run it reports on. Hooks live where runs end (`jobs/engine.py`, `chains/executor.py`,
  `plans/executor.py`) and in the cancel/kill endpoints.

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
- **Permissions are module-level, not object-level** — a user with *Mappings* can see and edit every mapping;
  there is no per-record ownership. Runs and notifications have no owner either (see *Notifications*). The admin
  API lists are unpaginated (fine for hundreds of users, not for tens of thousands).
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
