"""The Studio: one Pentaho-Spoon-style workspace for everything that used to
live on separate pages — Mappings (+ their canvas), Chains, Plans and the
Runs they produce. The page itself is a static shell (templates/studio/
index.html + static/js/studio/); everything it edits goes through the same
REST API the classic pages use, so this module only serves the shell and one
aggregate `tree` endpoint feeding the explorer sidebar."""
from django.db.models import Count
import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import render

from chains.models import CallChain, CallChainRun
from connections.models import Connection
from jobs.models import MigrationRun
from mappings.models import Mapping
from plans.models import MigrationPlan, PlanStep

from accounts import permissions

from . import templates as tpl

RECENT_RUNS_PER_KIND = 12


def studio(request):
    return render(request, "studio/index.html")


FALLBACK_ICON = "bi-hdd-network"      # a hand-configured connection with no App Store integration
PLAN_LOGO_LIMIT = 4


def _logo(connection):
    """The connector's App Store icon — uploaded logo if it has one, else its
    Bootstrap-icons class — the same one jobs/_route.html shows on the classic
    pages. The explorer and the open tabs draw these so a row says at a glance
    which systems it touches."""
    install = getattr(connection, "integration_install", None)
    integration = install.integration if install else None
    return {
        "name": connection.name,
        "icon": integration.icon if integration else FALLBACK_ICON,
        "image": integration.icon_image.url if integration and integration.icon_image else None,
    }


def _recent_runs(logos_by_doc):
    """Newest runs across mappings and chains, merged — the explorer's
    global "what just ran" list (what /jobs/ used to be for)."""
    rows = [
        {
            "kind": "mapping", "doc_id": r.mapping_id, "doc_name": r.mapping.name, "run_id": r.pk,
            "status": r.status, "started_at": r.started_at,
        }
        for r in MigrationRun.objects.select_related("mapping").order_by("-started_at")[:RECENT_RUNS_PER_KIND]
    ]
    rows += [
        {
            "kind": "chain", "doc_id": r.chain_id, "doc_name": r.chain.name, "run_id": r.pk,
            # A chain run row is created "failed" and only flipped once run_chain() finishes,
            # so an unfinished one is really still running.
            "status": r.status if r.finished_at else "running", "started_at": r.started_at,
        }
        for r in CallChainRun.objects.filter(chain__hidden=False).select_related("chain").order_by("-started_at")[:RECENT_RUNS_PER_KIND]
    ]
    rows.sort(key=lambda row: row["started_at"], reverse=True)
    rows = rows[:RECENT_RUNS_PER_KIND]
    for row in rows:
        row.update(logos_by_doc.get((row["kind"], row["doc_id"]), {"logos": [], "logos_to": []}))
    return rows


# The module that governs each kind of Studio document / template.
KIND_MODULE = {"mapping": "mappings", "chain": "chains", "plan": "plans", "run": "jobs"}


def tree(request):
    """Explorer data — only the sections the user's modules allow (the rest come back empty, and `modules`
    tells the Studio what is locked so it can say so instead of showing an empty list)."""
    can = lambda key: permissions.has_module(request.user, key)
    integration = "integration_install__integration"
    mappings = Mapping.objects.select_related(f"source_connection__{integration}").prefetch_related(
        f"destination_connections__{integration}", "entity_mappings__source_entity", "entity_mappings__target_entity",
    )
    chains = CallChain.objects.filter(hidden=False).select_related(f"connection__{integration}").prefetch_related("steps")
    plans = MigrationPlan.objects.prefetch_related(
        "steps", f"steps__mapping__source_connection__{integration}",
        f"steps__mapping__destination_connections__{integration}", f"steps__chain__connection__{integration}",
        f"steps__inline_step__connection__{integration}",
    )
    # How many plan steps point at each mapping/chain — deleting one silently deletes those steps.
    used_by_mapping = dict(PlanStep.objects.filter(mapping__isnull=False).values_list("mapping_id").annotate(n=Count("id")))
    used_by_chain = dict(PlanStep.objects.filter(chain__isnull=False).values_list("chain_id").annotate(n=Count("id")))

    logos_by_doc = {}
    mapping_rows = []
    for m in (mappings if can("mappings") else []):
        logos = {"logos": [_logo(m.source_connection)], "logos_to": [_logo(d) for d in m.destination_connections.all()]}
        logos_by_doc[("mapping", m.pk)] = logos
        mapping_rows.append({
            "id": m.pk, "name": m.name, "source": m.source_connection.name,
            "destinations": [d.name for d in m.destination_connections.all()],
            "pairs": len(m.entity_mappings.all()), "plan_steps": used_by_mapping.get(m.pk, 0), **logos,
            # Each entity pair, so a plan can be told to run just some of them.
            "pair_list": [{"id": em.pk, "source": em.source_entity.name, "target": em.target_entity.name} for em in m.entity_mappings.all()],
        })

    chain_rows = []
    for c in (chains if can("chains") else []):
        logos = {"logos": [_logo(c.connection)], "logos_to": []}
        logos_by_doc[("chain", c.pk)] = logos
        chain_rows.append({
            "id": c.pk, "name": c.name, "connection": c.connection.name,
            "steps": len(c.steps.all()), "plan_steps": used_by_chain.get(c.pk, 0), **logos,
        })

    plan_rows = []
    for p in (plans if can("plans") else []):
        # The systems a plan touches, in step order, each once.
        seen, plan_logos = set(), []
        for step in p.steps.all():
            if step.mapping_id:
                conns = [step.mapping.source_connection, *step.mapping.destination_connections.all()]
            elif step.chain_id:
                conns = [step.chain.connection]
            elif step.inline_step_id and step.inline_step.connection_id:
                conns = [step.inline_step.connection]        # a function block's request names its own connection
            else:
                continue                # a Wait (or a data-only block) touches no system
            for conn in conns:
                if conn.pk not in seen and len(plan_logos) < PLAN_LOGO_LIMIT:
                    seen.add(conn.pk)
                    plan_logos.append(_logo(conn))
        logos = {"logos": plan_logos, "logos_to": []}
        logos_by_doc[("plan", p.pk)] = logos
        plan_rows.append({"id": p.pk, "name": p.name, "status": p.status, "steps": len(p.steps.all()), **logos})

    return JsonResponse({
        "mappings": mapping_rows,
        "chains": chain_rows,
        "plans": plan_rows,
        "connections": [
            {"id": c.pk, "name": c.name, "connected": c.is_connected, "logos": [_logo(c)], "logos_to": []}
            for c in Connection.objects.select_related(integration).order_by("name")
        ],
        "recent_runs": [r for r in _recent_runs(logos_by_doc) if can("jobs" if r["kind"] == "mapping" else "chains")],
        "modules": sorted(permissions.effective_modules(request.user)),
    })


def template_catalog(request):
    """The starter templates the sidebar lists (metadata only — see studio/templates.py)."""
    allowed = [t for t in tpl.CATALOG if permissions.has_module(request.user, KIND_MODULE[t["kind"]])]
    return JsonResponse({"templates": allowed})


@require_POST
def template_apply(request, slug):
    """Create the mapping / chain / plan a template describes from the caller's answers.
    400 with a plain-language `error` when the answers don't add up."""
    template = next((t for t in tpl.CATALOG if t["slug"] == slug), None)
    if template is not None:
        module = KIND_MODULE[template["kind"]]
        if not permissions.has_module(request.user, module):
            return JsonResponse({"error": permissions.denial_message([module]), "permission_denied": True, "modules": [module]}, status=403)
    try:
        params = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Malformed request."}, status=400)
    try:
        return JsonResponse(tpl.apply_template(slug, params), status=201)
    except tpl.TemplateError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
