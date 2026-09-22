# Ante — User Guide

Ante moves data between systems. You connect the systems, say which record types and fields correspond, and Ante copies the data across — live, with progress, logs, and a way to stop it. This guide walks you through the whole thing, from your first migration to building multi-step API workflows and administering your team.

**Contents**

1. [The big picture](#1-the-big-picture)
2. [Getting around](#2-getting-around)
3. [Quick start: your first migration](#3-quick-start-your-first-migration)
4. [Connections and the App Store](#4-connections-and-the-app-store)
5. [Mappings: wiring fields together](#5-mappings-wiring-fields-together)
6. [Running a migration](#6-running-a-migration)
7. [The Studio](#7-the-studio)
8. [Chains: scripted API calls](#8-chains-scripted-api-calls)
9. [Plans and custom runs](#9-plans-and-custom-runs)
10. [Stopping things, and notifications](#10-stopping-things-and-notifications)
11. [Reports](#11-reports)
12. [Tickets and incidents](#12-tickets-and-incidents)
13. [Logs and Status](#13-logs-and-status)
14. [Your profile](#14-your-profile)
15. [For administrators: users, groups and permissions](#15-for-administrators-users-groups-and-permissions)
16. [Troubleshooting](#16-troubleshooting)
17. [Glossary](#17-glossary)

---

## 1. The big picture

Everything in Ante builds on five ideas:

| Idea | What it is | Example |
|---|---|---|
| **Connection** | A system you can talk to: its address and how to sign in. | "Shop API", "Customers.csv" |
| **Entity** | A kind of record inside a connection, made of **fields**. | `Customer` with `name`, `email`, `phone` |
| **Mapping** | A plan for moving data from a *source* connection to a *destination* connection. It holds **entity pairs** (source entity → destination entity) and, inside each pair, **field wires** (source field → destination field). | `Customer` → `Contact`, `name` → `full_name` |
| **Run** | One execution of a mapping. It reads from the source, transforms, and writes to the destination, recording every result. | "Run #42: 1,200 read, 1,198 written, 2 failed" |
| **Chain / Plan** | For when a plain field-to-field copy isn't enough. A **chain** is a scripted sequence of API calls; a **plan** sequences mappings, chains and pauses into one job. | "Create the customer, then create their order using the new id" |

The usual path is: **install a connector → create connections → discover entities → build a mapping → run it → check the results.**

---

## 2. Getting around

### The navbar

| Link | What's there |
|---|---|
| **Home** | Dashboard: total/active/successful/failed runs, your open tickets, active incidents, health per connection, top API endpoints. |
| **Status** | Platform health: connection status, API call volumes, busiest endpoints. |
| **App Store** | Install connectors and manage connections. |
| **Studio** | The visual workspace for mappings, chains, plans and runs (see [§7](#7-the-studio)). |
| **Mappings / Runs / Plans / Chains** | The classic list pages for each. Same data as the Studio, page by page. |
| **Reports** | Cross-entity CSV exports. |
| **Tickets / Incidents** | Support tickets and outage tracking. |
| **Logs** | Every outbound API call Ante has made. |
| **Admin** | Django admin. Only administrators see it. |

On the right of the bar: the **language** picker (English, Portuguese, Spanish, French, German, Italian, Japanese or Chinese — the whole app follows it), the **theme** toggle (light/dark), the **bell** (notifications), your **company logo**, your **photo** (click it to open your profile — see [§14](#14-your-profile)), and **Log out**.

**A padlock next to a link** means your administrator hasn't switched that module on for you. You can still click it: Ante takes you Home with a message explaining what's missing. See [§15](#15-for-administrators-users-groups-and-permissions) if you're the one who needs to grant access.

### On a phone

Ante is designed mobile-first: menus collapse into a drawer (the ☰ button), wide tables scroll inside their panel, and dialogs fit the screen. **The one exception is the Studio**, a drag-and-drop editor that needs room. On a small screen it shows a banner asking you to use a desktop or laptop; turning a tablet sideways or widening the window starts it.

### Signing in

Ante opens on a public front page describing the product; use **Sign in** there. There is no public sign-up: **an administrator creates your account** (see [§15](#15-for-administrators-users-groups-and-permissions)), and only existing, active accounts can sign in. If you don't have one, ask yours.

### First visit

A short welcome tour offers to walk you through the pages. Skip it any time.

---

## 3. Quick start: your first migration

Ten minutes, start to finish. You'll copy customers from one system into another. (No real API handy? Use two CSV connections — the built-in CSV connector needs no address or password.)

1. **Install a connector.** Open **App Store → Catalog**, pick the connector for your source system, and click **Install**. Fill in what it asks for (address and credentials, or nothing for file connectors). It now appears under **Installed**.
2. **Do the same for the destination.** You now have two connections.
3. **Discover entities.** Open a connection and use **Discover entities** to load its record types and fields (options in [§4](#discovering-entities)). Do this for both connections.
4. **Create a mapping.** Open the **Studio** (or **Mappings**) and create a new mapping: choose the source connection and the destination connection.
5. **Add an entity pair.** In the mapping's canvas, click **Add entity pair** and choose, say, source `Customer` and destination `Contact`. Two boxes appear, one per entity, each listing its fields.
6. **Wire the fields.** Drag from the **teal dot** on a source field to the **amber dot** on a destination field. Repeat for each field you want copied. Click a wire to give it a **transform** or delete it.
7. **Check with Data preview.** Open the mapping's **Data preview** tab: it shows real source rows next to the exact payloads Ante *would* send. **Nothing is written.** Fix anything that looks off.
8. **Run it.** Press **Run**. Watch counts, throughput and the live log.
9. **Read the results.** Open the run: what was read, written and failed, with a reason for each failed record. Fix the cause and **retry the failed ones**.

> **Shortcut:** in the Studio, the **Templates** section can do steps 4–6 for you: *Copy matching fields* wires every field whose name matches (ignoring case, `_`/`-`, and prefixes), and *Import a CSV / Excel file* sets up a file import in one go. See [§7](#templates).

---

## 4. Connections and the App Store

### Installing a connector

**App Store → Catalog** lists ready-made connectors grouped by category (E-commerce, ERP, Accounting, CRM, Notifications, File, Custom). Installing one creates a real connection, pre-filled with the right sign-in method. **App Store → Installed** shows what you've set up; from there you can open, edit or delete a connection. **Add connection** creates one from scratch when there's no ready-made connector.

### Sign-in methods

| Method | You provide | Notes |
|---|---|---|
| **OAuth2 (authorization code)** | Nothing up front — you're sent to the provider to approve access, then returned to Ante. | Tokens are refreshed automatically before they expire. |
| **Bearer token / API key** | The token. | Sent with every request. |
| **Basic auth** | Username and password. | |
| **JWT (self-signed)** | The signing configuration (**Save JWT config**). | |
| **No auth** | Nothing. | Public APIs, and file connectors. |

Secrets are **encrypted when stored** and are never sent back to your browser.

**Extra headers and parameters.** A connection can send custom headers and query parameters with *every* request, on top of its sign-in (for example a tenant id header).

**File connectors (CSV / Excel).** These have no address or password: you **upload a file** and its columns become fields.

### Discovering entities

Open a connection and use **Discover entities**. Pick how:

| Option | Use it when | What happens |
|---|---|---|
| **From OpenAPI** | The system publishes a Swagger/OpenAPI document. | Ante reads its schemas and creates entities and fields. |
| **From live sample** | There's no document, but you can call an endpoint. | Ante calls a real endpoint and infers fields and types from the response. |
| **From file** | It's a CSV/Excel connection. | Columns become fields. |
| **Manual** | You know exactly what you need. | **Create entity**, then add fields yourself. |

Re-discover after the other system changes.

### Automatic token refresh

For token-based connections, **Automatic token refresh** keeps a token fresh on a schedule. **Refresh now** does it immediately; **Delete job** stops it.

---

## 5. Mappings: wiring fields together

A mapping has a **source** and a **destination**, and holds one or more **entity pairs**.

### Creating, editing, duplicating and deleting mappings

On the **Mappings** (or **Canvas**) page: **New mapping** asks for a name, an optional description, the origin system and one or more destinations. Each row has **Open**, ✎ **edit** (rename, describe, change destinations), ⧉ **duplicate** (a copy with all its entity pairs and field wires) and 🗑 **delete**. The same actions are in the **⋯** menu of a mapping's page and, in the Studio, in the right-click menu and **Properties**.

Some rules protect what you've built: the **origin can't change once the mapping has entity pairs** (remove them, or duplicate the mapping); a **destination that a pair writes to can't be removed**; and a mapping **can't be deleted while a migration of it is running** (Kill it first). Deleting also removes its entity pairs, field mappings, run history and any plan step that used it.

### The canvas

- **Add entity pair** places a source entity and a destination entity on the board.
- Drag from a **teal dot** (source field) to an **amber dot** (destination field) to create a wire. Click a wire to edit or delete it.
- Entity boxes are draggable and remember their position.
- **Objects are shown in full.** If a source or destination entity has an object (an address, a list of items…), every level appears as an indented tree — `address`, `address.city`, `address.geo.lat` — and each one can be wired on its own. An object that shows nothing inside has a small button to **discover the fields inside** it; Ante also does this for you when you open a mapping.
- Pan by dragging empty space; **Ctrl/⌘ + scroll** zooms. The **?** button in the canvas corner lists every control.
- During and after a run, each entity shows what flowed through it (read / written / failed), and wires of a running pair carry moving dashes.

### Auto-mapping: let Ante suggest the wires

Instead of dragging every wire by hand, click **Auto-map** (the ✦ button on the mapping's page, or **Auto-map fields** in the Studio's canvas bar and *Design* tab). Ante looks at every entity pair of the mapping and, for each destination field, picks the source field that fits best **by name**:

- the same name (`email` → `email`), or the same name ignoring case and separators (`fullName` → `full_name`);
- the same field inside a different object (`address.city` → `location.city`);
- common equivalents, including Portuguese ones (`telefone` ≈ `phone`, `cep` ≈ `zip`, `name` ≈ `full_name`, `lat` ≈ `latitude`);
- shared key words and similar spellings. Names that share nothing but a generic word (`id`, `type`, `status`…) score too low to be suggested unless you choose *Loose*, and a suggestion whose types differ (a number into a text field…) is marked "types differ" and scored lower, so check those first. Objects are matched member by member (`address.city`), never as a whole, so an object isn't mapped twice.

**Nothing runs because of it.** Every suggestion is saved as a **draft**: it is drawn as a **dashed amber wire** with its confidence (`88%`) on it, and it is **left out of every run** until you accept it. To review them:

1. In the Studio, the **Review drafts** tab lists each suggestion with its types, a *High / Medium / Low* confidence and the reason it was suggested ("Same name", "Known equivalent names (telefone ≈ phone)"…). Tick the ones you trust and **Accept selected**, accept or reject one at a time (✓ / ✕, or click the wire), or **Accept all** / **Discard all**. Below the table, *Still not mapped* lists the fields Ante couldn't place, so you know what to wire by hand. **Preview with drafts** shows the output as if they were accepted.
2. On the classic page, a banner above the canvas offers **Accept all**, **Review one by one** (the *Raw* tab marks each draft and has **Accept suggestion / Reject suggestion** buttons) and **Discard all**. Clicking a dashed wire opens a dialog to accept or reject that one.

In the Studio, **Auto-map** asks how sure a match must be — *Strict* (same or almost the same name), *Balanced* (default; adds equivalent and related names) or *Loose* (weaker guesses) — and offers to leave target fields that are already mapped alone and to replace your earlier drafts. Wires you drew or accepted are never touched.

If you press **Run migration** while drafts are waiting, Ante tells you they'll be left out and asks whether to run anyway; the run log also says how many drafts were skipped. The **Data preview** leaves drafts out by default, with an **Include drafts** switch to see what accepting them would do.

### Transforms

A wire can carry a small **transform expression**. `value` stands for the source field's value:

| Goal | Expression |
|---|---|
| Upper-case | `value.upper()` |
| Trim spaces | `value.strip()` |
| First 10 characters | `value[:10]` |
| Round to 2 decimals | `round(value, 2)` |

Leave it blank to copy the value unchanged. Transforms run in a restricted evaluator — not arbitrary code — so a transform can't do anything except compute a value. A wire with a transform shows an **ƒ** badge.

### Data preview

The **Data preview** tab is a safe dry run: a sample of real source rows beside the exact payloads the wires would produce. Unmapped columns are dimmed and transformed cells are flagged. Use it before every first run.

---

## 6. Running a migration

Press **Run** on a mapping. It starts in the background and the page follows it live: requests per second, records read / written / failed, and a running log.

### Options

**Options** (next to Run) lets you set, per run:

- **Schedule** — start later instead of now.
- **Rate limit** — a cap on requests per second, so you don't overwhelm the destination.
- **Input files** — for file-based sources, the file to read.

You can also run **just one entity pair** from a mapping (see *Single run* and *Custom run* in [§7](#7-the-studio)), rather than everything.

### Reading a run

Open a run to see its per-entity step metrics, a throughput chart, the full log, and — for each failed record — *why* it failed. The mapping's **Runs** tab opens with a health dashboard: last sync, success rate, records written, average duration, and one column per run (click a column to inspect that run).

### When records fail

A run **finishes even if some records fail**; failures are counted and logged individually. Fix the cause (bad data, a wrong wire, a destination rule) and use **Retry** on the run: it re-runs only the entity pairs that failed. The **Runs** list page also supports bulk retry and delete, and filtering.

### Statuses

`pending` (queued) → `running` → `success` / `failed` / `cancelled`. A run you stop ends as **cancelled**, not failed. See [§10](#10-stopping-things-and-notifications).

---

## 7. The Studio

The Studio (`Studio` in the navbar) puts mappings, chains, plans and runs in one workspace, in the style of a desktop integration tool. It is **desktop only** (see [§2](#on-a-phone)).

### Layout

- **Toolbar** (top): **New**, **Run**, **Options**, **Kill**, reload, **Properties**, delete, zoom controls, and a button to show/hide the results panel.
- **Explorer** (left):
  - **View** tab — everything you can open: *Templates*, *Mappings*, *Chains*, *Plans*, *Recent runs*, *Connections*. Each row shows the logos of the systems it touches. Hover for ✎ (edit) and 🗑 (delete); right-click for a full menu. Use the filter box to search.
  - **Design** tab — a palette of blocks for whichever canvas is open (fields, step types, mappings to drop into a plan…).
- **Canvas** (centre): one **tab** per open document. Drag tabs to reorder them (the order is remembered); right-click a tab to move or close it.
- **Results** (bottom): follows the open tab — run history, per-step results, the live log. Drag the divider to resize, or hide it.

### Canvas controls

| Do this | To |
|---|---|
| Scroll | Pan |
| **Ctrl/⌘ + scroll** (or trackpad pinch) | Zoom |
| **Shift + scroll** | Pan sideways |
| Drag empty space | Pan |
| Drag a block | Move it |
| Right-click a block | Open its menu |
| **?** button (corner) | Show all of this |

### The New menu

**New** offers: a blank mapping, chain or plan; **From a template…**; **Single run…**; and **Custom run (drag & drop)…**.

### Templates

Templates are ready-made starting points; everything they create is an ordinary mapping, chain or plan you can rename, rewire or delete.

| Kind | Template | Does |
|---|---|---|
| Migration | **Copy matching fields** | Pairs two entities and wires every field whose name matches (case, `_`/`-` and prefixes ignored); reports what it couldn't match. |
| Migration | **Import a CSV / Excel file** | Sets up a file → system import. |
| Migration | **Sync one source to several destinations** | Creates pairs for each destination and wires matching fields. |
| Chain | **Create, then link** · **List, then fetch one** · **Start a job and wait for it** | Chains with captures and async polling pre-filled. |
| Plan | **Ordered plan** | Sequences existing mappings and chains. |
| Run | **Run a migration now** · **Schedule a migration** | Opens a mapping's run dialog. |

### Single run

*New → Single run…* picks one mapping and starts it with the run options (schedule, rate limit, files) — no need to open it first.

---

## 8. Chains: scripted API calls

A **chain** is an ordered list of steps. Use one when a simple field copy isn't enough: authenticate then fetch, create a record then use its id, poll a long job until it finishes.

A chain belongs to one **connection**, so every request goes to that system with its sign-in.

### Passing values between steps

Each step has a **name**. Later steps refer to earlier results with `{{step_name.path}}`:

```
Step "create_customer"  POST /customers          body: {"name": "Ada"}
Step "create_order"     POST /customers/{{create_customer.id}}/orders
```

Steps can also **capture** values from their response (for example an id) so later steps can use them. Placeholders work in the **path**, **body**, **headers** and **query parameters**.

### Step types

| Group | Step | What it does |
|---|---|---|
| Requests | **GET / POST / PUT / PATCH / DELETE** | An HTTP call, with an optional **timeout**, **headers** and **query parameters**. |
| Flow | **Wait** | Pause (up to 300 s in a chain). |
| Flow | **Async job** | The call only *starts* a job; poll a status path until a condition is true (for example `status = completed`), then optionally fetch a result path. You set the interval and a timeout. |
| Flow | **Next page** | Repeat a GET, following the response's next link or cursor, collecting every page's items. A link to a *different* host is never followed (your credentials would go with it). |
| Data | **Find in list** | Search a list an earlier step produced for the item whose field equals / contains / matches a value, then capture from it. |
| Data | **File preview** | Read the first rows of an uploaded CSV/Excel file, with a live preview in the dialog. |
| Checks | **Check header** | Assert that an earlier response's header exists / equals / matches. Either stop the chain or just warn. |

### Headers and query parameters

Any request step can send extra **headers** (the editor suggests common ones like `Authorization` and `Content-Type`) and **query parameters** (`?limit=50`). Values may use `{{placeholders}}`. They are merged with the connection's own headers, and secrets are masked in the recorded request.

They are set **per step**, in the Studio's step editor and on the classic chain page (the add-step row and each step's **Edit** dialog, under *Headers* and *Query parameters*). The steps table shows how many each step sends. Use query parameters for filters — for example `situacao = A` on a list request — and remember they are repeated on every page of a next-page step. A row with a value but no name is rejected; fully blank rows are ignored.

### Running and reading a chain

Press **Run**. Each step shows a tick or cross live; the results panel shows each step's request, response, response headers and captured variables. A failed step stops the chain.

---

## 9. Plans and custom runs

A **plan** runs several things in order as one job: mappings, single entity pairs, chains, pauses, and function blocks.

### Building one

*New → Custom run (drag & drop)…* opens a blank plan with the **Design** tab in front. Drag onto the canvas:

- a **mapping** (or expand it with **›** to drag just one of its **entity pairs**),
- a **chain**,
- a **Wait** (up to one hour),
- a **function block** — any chain step type (request, async job, next page, find in list, file preview, check header) dropped straight in.

Drop **on a step** to insert before it, on **empty space** to append. Drag a step onto another to reorder it. Double-click a step to edit what it runs.

### Function blocks share context

Function blocks live in one hidden chain per plan and run on **one shared context**, in plan order. So a *find* can search what an earlier request returned, a later request can use the id it found, and a *check* can read an earlier response's headers — even with a mapping run in between. Each request block names the connection it calls.

### Running, scheduling, re-running

Press **Execute**, or schedule it. A plan can be edited until it's executing or scheduled, so a finished run can be reshaped and run again; **each execution starts clean**. A failed step stops the plan.

---

## 10. Stopping things, and notifications

### The Kill button

The red **Kill** button in the Studio toolbar stops whatever the open document is running — a migration, a chain, or a whole plan (it also cancels a *scheduled* plan). The Runs table on a mapping has per-run **Kill / Cancel** buttons too.

Stopping is **cooperative**, so it isn't instant:

- a migration stops **between records**;
- a chain stops **between steps** and in the middle of a wait, poll or page loop;
- a plan stops **before its next step** and tells the step in progress to stop.

Records already written **stay written**, and a request already on the wire finishes or times out first. The run ends as **cancelled** (not failed) and can be retried. If a run got stuck as "running" because the server restarted mid-run, Kill closes it immediately.

### Notifications

Every run, chain run and plan that ends creates a notification:

| Outcome | Meaning |
|---|---|
| ✅ **Success** | It completed. |
| ❌ **Failed** | It ended with errors (a chain notification names the step that broke). |
| 🟠 **Cancelled** | It was called off *before it started* — a queued run or a scheduled plan. |
| 🟠 **Killed** | It was running and was stopped with Kill (or found orphaned and closed). |

- Scheduled jobs notify too, labelled as scheduled.
- **Who is told:** everyone who has the relevant module (Jobs, Chains or Plans) — runs don't belong to one person.
- **One notification per plan.** Runs that are part of a plan aren't announced separately; the plan's notification covers them.
- The **bell** shows the unread count and the latest few (it refreshes every 30 seconds while the tab is visible). On a phone the bell opens the full list. **Mark all read** clears it.
- `/notifications/` keeps the full history, with filters for outcome, type, and unread only. Clicking a notification marks it read and takes you to the run.

---

## 11. Reports

**Reports** builds CSV exports that combine data from several entities. Drag entities and their columns into sections, choose and order the columns, and see an on-screen preview before you export.

---

## 12. Tickets and incidents

### Tickets

Your personal support tickets. Each has a **priority** (low, medium, high, critical) and a **status** (open → in progress → resolved → closed), can carry attachments and notes, and can be **linked to an incident**. Filter by text, status and priority.

### Incidents

Tracks outages and issues that affect your integrations.

- **Severity:** low, medium, high, critical. **Status:** open → investigating → identified → resolved.
- Each incident has a **timeline** of notes.
- **SLA targets** are set from severity — first response and resolution — and shown as on-track or breached:

  | Severity | Respond within | Resolve within |
  |---|---|---|
  | Critical | 15 min | 4 h |
  | High | 60 min | 8 h |
  | Medium | 4 h | 24 h |
  | Low | 8 h | 72 h |

- After resolution, write a **post-mortem** (root cause and follow-ups).
- **Alert rules** open incidents automatically when a threshold is crossed: run failure rate, a connection's API error rate, or the global API error rate — each over a look-back window you choose, with a severity.

Incidents are a separate module, **off by default** for ordinary users; an administrator can switch it on.

---

## 13. Logs and Status

### Logs

**Logs** is the history of **every outbound API call** Ante has made, across all connections: time, connection, run, method, URL, status and duration. Request and response bodies are stored too, so they are searchable. Use the quick filters (errors in the last hour, slow calls, and so on) or the one-line **query box**. Terms are space-separated and combined with AND; quote values containing spaces.

| Query | Finds |
|---|---|
| `status:200` · `status:>=400` · `status:!=200` | By status code |
| `method:POST` | By HTTP method |
| `url:produtos` | URL contains text |
| `connection:shop` | By connection name (contains) |
| `run:91` | Calls made by run 91 |
| `error:true` | Calls that had an error |
| `duration:>500` | Slower than 500 ms |
| `since:1h` (also `30m`, `2d`, `45s`) | Only recent calls |
| `json.id:42` · `json.data.email:"a@b.com"` | A value inside the JSON response |
| `timeout` (a bare word) | Free-text search over URL, bodies and errors |

Example: `connection:shop status:>=400 since:1h` — everything the Shop connection got wrong in the last hour.

### Status

**Status** shows connection health, API call volumes over time, and the busiest endpoints — the first place to look when something feels slow.

---

## 14. Your profile

Click your photo in the navbar.

- **Personal:** photo, first and last name, email.
- **Company:** company **logo**, name and document number (for example a tax id). The logo appears in the navbar, as a round badge next to your photo, so the app shows whose workspace it is.
- Images: PNG, JPG, GIF, WEBP or SVG, up to 1 MB. A wide logo is cropped to fill the circle, so a roughly square one looks best.
- **Administrators** also see the **Administration** panel here ([§15](#15-for-administrators-users-groups-and-permissions)).

---

## 15. For administrators: users, groups and permissions

You're an **administrator** if your account is marked *staff* or *superuser*. Administrators can use every module and see an **Administration** section at the bottom of their **Profile** page.

### How permissions work

Ante is split into **modules**, each simply **on or off** for a person:

| Module | Covers |
|---|---|
| Status | The Status page |
| App Store & connections | Installing connectors, connections, entities |
| Mappings | Mappings, entity pairs, field wires |
| Jobs (runs) | Starting, watching, retrying and killing runs |
| Plans | Plans and custom runs |
| Chains | Chains and their runs |
| Studio | The Studio workspace |
| Reports | Reports |
| Tickets | Tickets |
| Incidents | Incidents, post-mortems, alert rules |
| Logs | The API call log |

A person's access is:

> **(modules of all their groups + modules switched on for them) − modules switched off for them**

**Off always wins.** Administrators have everything, regardless.

### The three building blocks

- **Groups** — named sets of modules ("Operators: Jobs, Plans, Chains"). Give people the groups that fit their job. A person can be in several.
- **Departments** — where someone sits in the organisation. A label for your own organising; it doesn't change what anyone can do.
- **Per-user overrides** — for the exceptions. For each module a user is **Inherit** (use their groups), **On** (allow just for them), or **Off** (block just for them).

A built-in group, **Standard user**, gives everything except Incidents, and is added to every new user (and was added to every existing user when permissions were introduced, so nobody lost access). Edit it, remove people from it, or mark any group **"Add to every newly created user"** to change the default.

### The Administration panel

On your **Profile** page, three tabs:

**Users** — search, filter by department or group, **New user**, edit, delete. When you edit a user you set:

- username, name, email, and (optionally) a new password — passwords must pass the usual strength rules;
- **Active** (may sign in) — deactivate rather than delete to keep history;
- **Administrator** and, if you're a superuser, **Superuser**;
- department and groups;
- every module as **Inherit / On / Off**, with a live readout: *"On via group"*, *"On (set for this user)"*, *"Off"*…

**Groups** — create and edit groups with an on/off switch per module (and *All on / All off*). Deleting a group removes the access it gave (the dialog tells you how many people are affected).

**Departments** — name and description. Deleting one leaves its members without a department.

### Safety rails

- You can't deactivate, demote or delete **yourself**.
- The **last active administrator** can never be removed.
- Only a **superuser** can grant or remove superuser status, or change a superuser's account.

### What users see when blocked

A page they can't use sends them Home with a warning ("You don't have permission to use Mappings. Ask an administrator to enable it for your account."). In the Studio, the section is locked, the menu entry shows a padlock, and any attempt shows a warning toast. Changes take effect on the user's **next request** — no need for them to sign out.

> **Note:** permissions are per module, not per record. Someone with *Mappings* can see and edit every mapping. Runs and notifications also aren't owned by one person.

---

## 16. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "You don't have permission to use …" | The module is off for you. Ask an administrator (see [§15](#15-for-administrators-users-groups-and-permissions)). |
| Studio shows only a banner about desktop | The window is narrower than 768 px. Use a desktop/laptop, or widen the window. |
| No entities to choose for a mapping | Run **Discover entities** on that connection first ([§4](#discovering-entities)). |
| A connection can't be reached | Check the address and sign-in on its page; look in **Logs** for `connection:name error:true` to see the exact response. |
| Some records failed | Open the run: each failed record has a reason. Fix the cause and **Retry**. |
| Destination rejects the data | Use **Data preview** to inspect the exact payload; adjust the wires or a transform. |
| A transform errors | Transforms are limited expressions on `value` (`value.upper()`, `value[:10]`, `round(value, 2)`). Check the source value's type. |
| A run shows "running" forever | The server was probably restarted mid-run. Press **Kill**: it closes orphaned runs immediately. |
| Kill didn't stop it *instantly* | Stopping is cooperative — see [§10](#10-stopping-things-and-notifications). It stops at the next safe point. |
| A chain step can't find a value | Check the step **name** in `{{name.path}}` and that the earlier step ran first and captured it; see the request/response in the results panel. |
| Nobody got a notification | Only users with the Jobs / Chains / Plans module are told, and runs inside a plan are reported by the plan. |
| A page shows a padlock link | See "You don't have permission…" above. |

---

## 17. Glossary

- **Connection** — a system you can reach, with its sign-in.
- **Integration** — a ready-made connector in the App Store; installing it creates a connection.
- **Entity / field** — a record type and its attributes.
- **Mapping** — source connection + destination connection + entity pairs + field wires.
- **Entity pair** — one source entity paired with one destination entity.
- **Wire** — a source field → destination field link, optionally with a transform.
- **Draft wire** — a wire suggested by auto-mapping and not accepted yet; shown dashed, and left out of runs until accepted.
- **Transform** — a small expression on `value` applied as a record is copied.
- **Run** — one execution of a mapping.
- **Chain** — an ordered list of steps (API calls and helpers) on one connection.
- **Capture / placeholder** — saving a value from a response, and using it later as `{{step.path}}`.
- **Plan** — an ordered job made of mappings, entity pairs, chains, waits and function blocks.
- **Function block** — a chain step used directly inside a plan.
- **Kill** — ask a running migration, chain or plan to stop at its next safe point.
- **Module** — one switchable area of the app (Mappings, Jobs, …).
- **Group** — a named set of modules. **Department** — an organisational label.
- **Administrator** — a user with every module who manages users, groups and departments.
