#!/usr/bin/env python3
"""Azure DevOps Dashboard Generator — OntimeCorporate"""

import json
import base64
import urllib.parse
import http.client
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import os

ORG = "OntimeCorporate"
PAT = os.environ.get("ADO_PAT", "")
if not PAT:
    raise ValueError("ADO_PAT environment variable not set")
AUTH = base64.b64encode(f":{PAT}".encode()).decode()
HEADERS = {"Authorization": f"Basic {AUTH}", "Accept": "application/json"}

CLOSED_STATES = {"Closed", "Removed", "Done", "Resolved"}
MY_USER = "jarbizu@ontime.es"

HEADERS_JSON = {**{"Content-Type": "application/json"}, **{"Authorization": f"Basic {AUTH}", "Accept": "application/json"}}


def _request(host, path):
    conn = http.client.HTTPSConnection(host, timeout=20)
    conn.request("GET", path, headers=HEADERS)
    r = conn.getresponse()
    body = r.read().decode("utf-8")
    conn.close()
    return json.loads(body)


def _post(host, path, payload):
    conn = http.client.HTTPSConnection(host, timeout=20)
    conn.request("POST", path, body=json.dumps(payload), headers=HEADERS_JSON)
    r = conn.getresponse()
    body = r.read().decode("utf-8")
    conn.close()
    return json.loads(body)


def get_mentions():
    """Search all work items where current user is @mentioned."""
    results = []
    seen = set()
    for query in [f"@{MY_USER.split('@')[0]}", MY_USER]:
        data = _post("almsearch.dev.azure.com",
            "/OntimeCorporate/_apis/search/workitemsearchresults?api-version=7.0",
            {"searchText": query, "$top": 200, "$skip": 0, "filters": {}, "includeFacets": False})
        for item in data.get("results", []):
            wid = item["fields"]["system.id"]
            if wid not in seen:
                seen.add(wid)
                results.append(item)
    return results


def get_projects():
    data = _request("dev.azure.com", f"/{ORG}/_apis/projects?api-version=7.0&$top=100")
    return data["value"]


def _analytics(project_name, odata_path):
    enc = urllib.parse.quote(project_name, safe="")
    return _request("analytics.dev.azure.com", f"/{ORG}/{enc}/_odata/v3.0/{odata_path}")


def get_work_item_states(project_name):
    try:
        data = _analytics(project_name,
            "WorkItems?$apply=groupby((State),aggregate($count%20as%20Count))")
        return {item["State"]: item["Count"] for item in data.get("value", [])}
    except Exception:
        return {}


def get_unplanned(project_name):
    """Items whose IterationPath == project root (no sprint assigned), excluding closed states."""
    try:
        proj_enc = urllib.parse.quote(project_name.replace("'", "''"), safe="")
        apply = (
            f"filter(Iteration/IterationPath%20eq%20'{proj_enc}'"
            f"%20and%20State%20ne%20'Closed'"
            f"%20and%20State%20ne%20'Removed'"
            f"%20and%20State%20ne%20'Done'"
            f"%20and%20State%20ne%20'Resolved')"
            f"/groupby((State,WorkItemType),aggregate($count%20as%20Count))"
        )
        data = _analytics(project_name, f"WorkItems?$apply={apply}")
        items = data.get("value", [])
        total = sum(i["Count"] for i in items)
        by_type = {}
        by_state = {}
        for i in items:
            t = i["WorkItemType"]
            s = i["State"]
            c = i["Count"]
            by_type[t] = by_type.get(t, 0) + c
            by_state[s] = by_state.get(s, 0) + c
        return {"total": total, "by_type": by_type, "by_state": by_state}
    except Exception:
        return {"total": 0, "by_type": {}, "by_state": {}}


def relative_time(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        if days == 0: return "Hoy"
        if days == 1: return "Ayer"
        if days < 30: return f"Hace {days}d"
        if days < 365: return f"Hace {days // 30}m"
        return f"Hace {days // 365}a"
    except Exception:
        return iso[:10]


STATE_COLORS = {
    "New": "#6366f1", "Active": "#3b82f6", "Doing": "#f59e0b",
    "In Progress": "#f59e0b", "Blocked": "#ef4444", "Test": "#8b5cf6",
    "Resolved": "#10b981", "Done": "#10b981", "Closed": "#6b7280", "Removed": "#475569",
}
TYPE_ICONS = {
    "Epic": "⚡", "Feature": "★", "User Story": "📖", "Task": "✓",
    "Bug": "🐛", "Test Case": "🧪", "Petición": "📋",
}

def state_color(s):
    for k, v in STATE_COLORS.items():
        if k.lower() in s.lower():
            return v
    return "#94a3b8"

def activity_class(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - dt).days
        if days <= 7: return "hot"
        if days <= 30: return "warm"
        if days <= 90: return "cool"
        return "cold"
    except Exception:
        return "cold"


def build_html(projects, stats, unplanned, mentions):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    total_projects = len(projects)
    total_wi = sum(sum(s.values()) for s in stats.values())
    total_unplanned = sum(u["total"] for u in unplanned.values())

    # ---- TAB 1: Overview cards ----
    cards = ""
    for p in sorted(projects, key=lambda x: x["lastUpdateTime"], reverse=True):
        name = p["name"]
        last = p.get("lastUpdateTime", "")
        desc = p.get("description", "")
        states = stats.get(name, {})
        total = sum(states.values())
        ac = activity_class(last)
        unp = unplanned.get(name, {}).get("total", 0)
        unp_pct = f"{unp/total*100:.0f}%" if total > 0 else "—"
        unp_badge = f'<span class="unp-badge" title="Sin planificación (activos)">{unp} sin sprint</span>' if unp > 0 else ""

        pills = "".join(
            f'<span class="pill" style="background:{state_color(s)}22;color:{state_color(s)};border:1px solid {state_color(s)}55">{s} <b>{c}</b></span>'
            for s, c in sorted(states.items(), key=lambda x: -x[1])
        )
        bar = "".join(
            f'<div class="bar-seg" style="width:{c/total*100:.1f}%;background:{state_color(s)}" title="{s}: {c}"></div>'
            for s, c in sorted(states.items(), key=lambda x: -x[1])
        ) if total > 0 else ""

        desc_html = f'<p class="desc">{desc[:120]}{"…" if len(desc)>120 else ""}</p>' if desc else ""

        cards += f"""
        <div class="card {ac}" data-name="{name.lower()}">
          <div class="card-header">
            <span class="activity-dot {ac}"></span>
            <h3>{name}</h3>
            <span class="ts">{relative_time(last)}</span>
          </div>
          {desc_html}
          <div class="bar-wrap">{bar or '<span class="no-data">Sin work items</span>'}</div>
          <div class="pills">{pills or '<span class="no-data">—</span>'}</div>
          <div class="card-footer">
            <span class="total-lbl">{"<b>" + str(total) + "</b> items" if total else ""}</span>
            {unp_badge}
          </div>
        </div>"""

    # ---- TAB 2: Sin planificación ----
    sorted_unp = sorted(
        [(p["name"], unplanned.get(p["name"], {"total": 0, "by_type": {}, "by_state": {}}))
         for p in projects],
        key=lambda x: -x[1]["total"]
    )

    unp_rows = ""
    for name, u in sorted_unp:
        if u["total"] == 0:
            continue
        total_proj = sum(stats.get(name, {}).values())
        pct = u["total"] / total_proj * 100 if total_proj > 0 else 0
        bar_w = f"{min(pct, 100):.1f}%"

        type_pills = "".join(
            f'<span class="pill" style="background:#6366f122;color:#a5b4fc;border:1px solid #6366f155">'
            f'{TYPE_ICONS.get(t,"•")} {t} <b>{c}</b></span>'
            for t, c in sorted(u["by_type"].items(), key=lambda x: -x[1])
        )
        state_pills = "".join(
            f'<span class="pill" style="background:{state_color(s)}22;color:{state_color(s)};border:1px solid {state_color(s)}55">{s} <b>{c}</b></span>'
            for s, c in sorted(u["by_state"].items(), key=lambda x: -x[1])
        )

        unp_rows += f"""
        <div class="unp-row" data-name="{name.lower()}">
          <div class="unp-header">
            <span class="unp-name">{name}</span>
            <span class="unp-count">{u['total']:,} <span class="unp-sub">sin sprint</span></span>
            <span class="unp-pct">{pct:.0f}% del total</span>
          </div>
          <div class="unp-bar-wrap"><div class="unp-bar" style="width:{bar_w}"></div></div>
          <div class="unp-detail">
            <div class="pills" style="margin-bottom:4px">{type_pills}</div>
            <div class="pills">{state_pills}</div>
          </div>
        </div>"""

    projects_with_unp = sum(1 for _, u in sorted_unp if u["total"] > 0)

    # ---- TAB 3: Menciones ----
    ACTIVE_STATES = {"New", "Active", "Doing", "In Progress", "Blocked", "Test", "Estimate"}
    FIELD_LABELS = {
        "system.description": "Descripción",
        "system.history": "Comentario",
        "system.createdby": "Creado por",
        "system.assignedto": "Asignado a",
        "system.title": "Título",
    }

    def strip_html(text):
        import re
        text = re.sub(r'<[^>]+>', '', text or '')
        return text[:200].strip()

    def mention_highlight(hits):
        parts = []
        for h in hits[:2]:
            label = FIELD_LABELS.get(h["fieldReferenceName"], h["fieldReferenceName"])
            for hl in h.get("highlights", [])[:1]:
                clean = strip_html(hl).replace("<highlightpre>","").replace("</highlightpre>","")
                # wrap the @mention in a highlight span
                import re
                clean = re.sub(r'(@jarbizu|jarbizu@ontime\.es)', r'<mark>\1</mark>', clean)
                parts.append(f'<span class="hit-label">{label}:</span> <span class="hit-text">{clean}</span>')
        return " &nbsp;·&nbsp; ".join(parts) if parts else ""

    # Sort: active first, then by changed date
    def mention_sort_key(m):
        state = m["fields"]["system.state"]
        active = 0 if state in ACTIVE_STATES else 1
        return (active, m["fields"].get("system.changeddate", ""))

    sorted_mentions = sorted(mentions, key=mention_sort_key, reverse=False)
    active_mentions = [m for m in sorted_mentions if m["fields"]["system.state"] in ACTIVE_STATES]
    closed_mentions = [m for m in sorted_mentions if m["fields"]["system.state"] not in ACTIVE_STATES]

    def render_mention_row(m):
        f = m["fields"]
        wid = f["system.id"]
        wtype = f["system.workitemtype"]
        title = f["system.title"][:80]
        state = f["system.state"]
        project = m["project"]["name"]
        assigned = f.get("system.assignedto", "—").split("<")[0].strip()
        changed = f.get("system.changeddate", "")[:10]
        hits = m.get("hits", [])
        sc = state_color(state)
        hl = mention_highlight(hits)
        fields_hit = ", ".join(set(FIELD_LABELS.get(h["fieldReferenceName"], h["fieldReferenceName"]) for h in hits))
        url = f"https://dev.azure.com/OntimeCorporate/{urllib.parse.quote(project)}/_workitems/edit/{wid}"
        return f"""
        <div class="mention-row" data-state="{state.lower()}" data-project="{project.lower()}">
          <div class="mention-header">
            <span class="mention-type">{wtype}</span>
            <a class="mention-title" href="{url}" target="_blank">#{wid} {title}</a>
            <span class="pill" style="background:{sc}22;color:{sc};border:1px solid {sc}55;flex-shrink:0">{state}</span>
          </div>
          <div class="mention-meta">
            <span class="mention-project">📁 {project}</span>
            <span class="mention-assigned">👤 {assigned}</span>
            <span class="mention-fields">🔍 en: {fields_hit}</span>
            <span class="mention-date">🕐 {changed}</span>
          </div>
          {f'<div class="mention-hl">{hl}</div>' if hl else ""}
        </div>"""

    mention_rows_active = "".join(render_mention_row(m) for m in active_mentions)
    mention_rows_closed = "".join(render_mention_row(m) for m in closed_mentions)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Azure DevOps — OntimeCorporate</title>
<script src="https://identity.netlify.com/v1/netlify-identity-widget.js"></script>
<style>
  :root {{
    --bg:#0f172a; --surface:#1e293b; --surface2:#334155;
    --text:#f1f5f9; --muted:#94a3b8; --accent:#6366f1;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}}
  header{{padding:20px 32px;border-bottom:1px solid #1e293b;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
  header h1{{font-size:1.3rem;font-weight:700}} header h1 span{{color:var(--accent)}}
  .meta{{color:var(--muted);font-size:.82rem}}
  .summary{{display:flex;gap:16px;padding:16px 32px;flex-wrap:wrap}}
  .stat{{background:var(--surface);border-radius:10px;padding:14px 20px;flex:1;min-width:120px}}
  .stat .num{{font-size:1.8rem;font-weight:700;color:var(--accent)}}
  .stat.warn .num{{color:#f59e0b}}
  .stat .lbl{{color:var(--muted);font-size:.8rem;margin-top:2px}}
  /* tabs */
  .tabs{{display:flex;gap:4px;padding:0 32px;margin-bottom:16px;border-bottom:1px solid var(--surface2)}}
  .tab{{padding:10px 18px;cursor:pointer;font-size:.9rem;color:var(--muted);border-bottom:2px solid transparent;transition:all .15s}}
  .tab:hover{{color:var(--text)}}
  .tab.active{{color:var(--accent);border-bottom-color:var(--accent)}}
  .tab-content{{display:none}} .tab-content.active{{display:block}}
  /* filter bar */
  .filter-bar{{padding:0 32px 14px;display:flex;gap:8px;flex-wrap:wrap}}
  .filter-bar input{{background:var(--surface);border:1px solid var(--surface2);color:var(--text);padding:7px 13px;border-radius:8px;font-size:.88rem;width:240px;outline:none}}
  .filter-bar input:focus{{border-color:var(--accent)}}
  .btn-filter{{background:var(--surface);border:1px solid var(--surface2);color:var(--muted);padding:7px 13px;border-radius:8px;cursor:pointer;font-size:.82rem;transition:all .15s}}
  .btn-filter:hover,.btn-filter.active{{background:var(--accent);border-color:var(--accent);color:#fff}}
  /* overview grid */
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px;padding:0 32px 32px}}
  .card{{background:var(--surface);border-radius:12px;padding:16px;border:1px solid transparent;transition:transform .15s,border-color .15s}}
  .card:hover{{transform:translateY(-2px);border-color:var(--surface2)}}
  .card.hot{{border-left:3px solid #10b981}} .card.warm{{border-left:3px solid #f59e0b}}
  .card.cool{{border-left:3px solid #6366f1}} .card.cold{{border-left:3px solid #475569}}
  .card-header{{display:flex;align-items:center;gap:7px;margin-bottom:8px}}
  .card-header h3{{font-size:.92rem;font-weight:600;flex:1;line-height:1.3}}
  .ts{{color:var(--muted);font-size:.76rem;white-space:nowrap}}
  .activity-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
  .activity-dot.hot{{background:#10b981;box-shadow:0 0 6px #10b981}}
  .activity-dot.warm{{background:#f59e0b}} .activity-dot.cool{{background:#6366f1}} .activity-dot.cold{{background:#475569}}
  .desc{{color:var(--muted);font-size:.78rem;margin-bottom:9px;line-height:1.5}}
  .bar-wrap{{height:7px;border-radius:4px;overflow:hidden;background:var(--surface2);display:flex;margin-bottom:10px}}
  .bar-seg{{height:100%}}
  .pills{{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:7px}}
  .pill{{font-size:.73rem;padding:2px 7px;border-radius:99px;font-weight:500}}
  .card-footer{{display:flex;align-items:center;justify-content:space-between}}
  .total-lbl{{color:var(--muted);font-size:.78rem}} .total-lbl b{{color:var(--text)}}
  .unp-badge{{font-size:.72rem;background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b55;padding:2px 7px;border-radius:99px}}
  .no-data{{color:var(--muted);font-size:.78rem}}
  .hidden{{display:none!important}}
  /* unplanned tab */
  .unp-list{{padding:0 32px 32px;display:flex;flex-direction:column;gap:12px}}
  .unp-row{{background:var(--surface);border-radius:12px;padding:16px;border-left:3px solid #f59e0b}}
  .unp-header{{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}}
  .unp-name{{font-weight:600;font-size:.95rem;flex:1}}
  .unp-count{{font-size:1.3rem;font-weight:700;color:#f59e0b}}
  .unp-sub{{font-size:.75rem;font-weight:400;color:var(--muted)}}
  .unp-pct{{font-size:.8rem;color:var(--muted)}}
  .unp-bar-wrap{{height:6px;background:var(--surface2);border-radius:3px;overflow:hidden;margin-bottom:10px}}
  .unp-bar{{height:100%;background:linear-gradient(90deg,#f59e0b,#ef4444);border-radius:3px}}
  .unp-detail{{display:flex;flex-direction:column;gap:4px}}
  .unp-total-summary{{background:var(--surface);border-radius:10px;padding:14px 20px;margin:0 32px 16px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}}
  .unp-total-summary .big{{font-size:2rem;font-weight:700;color:#f59e0b}}
  .unp-total-summary .lbl{{color:var(--muted);font-size:.85rem}}
  /* mentions tab */
  .mention-list{{padding:0 32px 32px;display:flex;flex-direction:column;gap:10px}}
  .mention-section-title{{padding:0 32px;margin-bottom:10px;font-size:.85rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}
  .mention-row{{background:var(--surface);border-radius:10px;padding:14px 16px;border-left:3px solid var(--accent);transition:border-color .15s}}
  .mention-row[data-state="closed"],.mention-row[data-state="removed"],.mention-row[data-state="done"],.mention-row[data-state="resolved"]{{border-left-color:#475569;opacity:.7}}
  .mention-header{{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}}
  .mention-type{{font-size:.72rem;background:#6366f122;color:#a5b4fc;border:1px solid #6366f144;padding:1px 7px;border-radius:99px;white-space:nowrap}}
  .mention-title{{color:var(--text);text-decoration:none;font-size:.9rem;font-weight:600;flex:1;min-width:0}}
  .mention-title:hover{{color:var(--accent)}}
  .mention-meta{{display:flex;gap:14px;flex-wrap:wrap;font-size:.78rem;color:var(--muted);margin-bottom:6px}}
  .mention-project{{color:#94a3b8}}
  .mention-hl{{font-size:.8rem;color:var(--muted);background:var(--surface2);padding:6px 10px;border-radius:6px;line-height:1.5}}
  .mention-hl mark{{background:#f59e0b33;color:#fcd34d;padding:0 2px;border-radius:2px}}
  .hit-label{{color:var(--accent);font-weight:600}}
  .hit-text{{color:var(--muted)}}
  .mention-summary{{background:var(--surface);border-radius:10px;padding:14px 20px;margin:0 32px 16px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}}
  .mention-summary .big{{font-size:2rem;font-weight:700;color:var(--accent)}}
  .mention-summary .big.warn{{color:#f59e0b}}
  .mention-summary .lbl{{color:var(--muted);font-size:.82rem}}
</style>
</head>
<body>
<header>
  <h1>Azure DevOps — <span>OntimeCorporate</span></h1>
  <span class="meta">Actualizado: {now}</span>
</header>
<div class="summary">
  <div class="stat"><div class="num">{total_projects}</div><div class="lbl">Proyectos</div></div>
  <div class="stat"><div class="num">{total_wi:,}</div><div class="lbl">Work Items totales</div></div>
  <div class="stat warn"><div class="num">{total_unplanned:,}</div><div class="lbl">Sin planificación (activos)</div></div>
  <div class="stat"><div class="num">{sum(1 for p in projects if activity_class(p.get("lastUpdateTime",""))=="hot")}</div><div class="lbl">Activos esta semana</div></div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab(this,'overview')">Vista general</div>
  <div class="tab" onclick="switchTab(this,'unplanned')">Sin planificación <span style="background:#f59e0b33;color:#f59e0b;padding:1px 7px;border-radius:99px;font-size:.75rem;margin-left:4px">{total_unplanned:,}</span></div>
  <div class="tab" onclick="switchTab(this,'mentions')">Menciones <span style="background:#6366f133;color:#a5b4fc;padding:1px 7px;border-radius:99px;font-size:.75rem;margin-left:4px">{len(mentions)}</span></div>
</div>

<!-- TAB: Vista general -->
<div id="tab-overview" class="tab-content active">
  <div class="filter-bar">
    <input type="text" id="search-overview" placeholder="Buscar proyecto..." oninput="filterOverview()">
    <button class="btn-filter active" onclick="filterActivity(this,'all')">Todos</button>
    <button class="btn-filter" onclick="filterActivity(this,'hot')">Esta semana</button>
    <button class="btn-filter" onclick="filterActivity(this,'warm')">Este mes</button>
    <button class="btn-filter" onclick="filterActivity(this,'cold')">Inactivos</button>
  </div>
  <div class="grid" id="grid">{cards}</div>
</div>

<!-- TAB: Sin planificación -->
<div id="tab-unplanned" class="tab-content">
  <div class="unp-total-summary">
    <div><div class="big">{total_unplanned:,}</div><div class="lbl">work items activos sin sprint en {projects_with_unp} proyectos</div></div>
  </div>
  <div class="filter-bar">
    <input type="text" id="search-unplanned" placeholder="Buscar proyecto..." oninput="filterUnplanned()">
  </div>
  <div class="unp-list" id="unp-list">{unp_rows}</div>
</div>

<!-- TAB: Menciones -->
<div id="tab-mentions" class="tab-content">
  <div class="mention-summary">
    <div><div class="big">{len(mentions)}</div><div class="lbl">menciones totales</div></div>
    <div><div class="big warn">{len(active_mentions)}</div><div class="lbl">en items activos</div></div>
    <div><div class="big" style="color:#6b7280">{len(closed_mentions)}</div><div class="lbl">en items cerrados</div></div>
  </div>
  <div class="filter-bar">
    <input type="text" id="search-mentions" placeholder="Buscar proyecto o título..." oninput="filterMentions()">
    <button class="btn-filter active" onclick="filterMentionState(this,'all')">Todos</button>
    <button class="btn-filter" onclick="filterMentionState(this,'active')">Activos</button>
    <button class="btn-filter" onclick="filterMentionState(this,'closed')">Cerrados</button>
  </div>
  <p class="mention-section-title" id="mention-active-label">Activos ({len(active_mentions)})</p>
  <div class="mention-list" id="mention-list-active">{mention_rows_active}</div>
  <p class="mention-section-title" style="margin-top:8px" id="mention-closed-label">Cerrados / Resueltos ({len(closed_mentions)})</p>
  <div class="mention-list" id="mention-list-closed">{mention_rows_closed}</div>
</div>

<script>
  let activeFilter = 'all';
  let mentionFilter = 'all';

  function switchTab(el, id) {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('tab-' + id).classList.add('active');
  }}

  function filterOverview() {{
    const q = document.getElementById('search-overview').value.toLowerCase();
    document.querySelectorAll('#grid .card').forEach(c => {{
      const match = c.dataset.name.includes(q);
      const matchAct = activeFilter === 'all' || c.classList.contains(activeFilter);
      c.classList.toggle('hidden', !match || !matchAct);
    }});
  }}

  function filterActivity(btn, filter) {{
    activeFilter = filter;
    document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    filterOverview();
  }}

  function filterUnplanned() {{
    const q = document.getElementById('search-unplanned').value.toLowerCase();
    document.querySelectorAll('#unp-list .unp-row').forEach(r => {{
      r.classList.toggle('hidden', !r.dataset.name.includes(q));
    }});
  }}

  function filterMentions() {{
    const q = document.getElementById('search-mentions').value.toLowerCase();
    document.querySelectorAll('.mention-row').forEach(r => {{
      const text = (r.dataset.project + ' ' + r.querySelector('.mention-title').textContent).toLowerCase();
      const matchQ = text.includes(q);
      const matchState = mentionFilter === 'all' ||
        (mentionFilter === 'active' && !['closed','removed','done','resolved'].includes(r.dataset.state)) ||
        (mentionFilter === 'closed' && ['closed','removed','done','resolved'].includes(r.dataset.state));
      r.classList.toggle('hidden', !matchQ || !matchState);
    }});
  }}

  function filterMentionState(btn, filter) {{
    mentionFilter = filter;
    document.querySelectorAll('#tab-mentions .btn-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    filterMentions();
  }}
</script>
<script>
  // Netlify Identity — proteger el dashboard
  if (window.netlifyIdentity) {{
    window.netlifyIdentity.on('init', user => {{
      if (!user) {{
        window.netlifyIdentity.on('login', () => location.reload());
        window.netlifyIdentity.open();
      }}
    }});
  }}
</script>
</body>
</html>"""


def main():
    print("Obteniendo proyectos...")
    projects = get_projects()
    print(f"  {len(projects)} proyectos encontrados")

    print("Cargando work items por estado (en paralelo)...")
    stats = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(get_work_item_states, p["name"]): p["name"] for p in projects}
        for i, f in enumerate(as_completed(futures), 1):
            name = futures[f]
            stats[name] = f.result()
            print(f"  [{i}/{len(projects)}] {name}: {sum(stats[name].values())} items")

    print("Cargando items sin planificación (en paralelo)...")
    unplanned = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(get_unplanned, p["name"]): p["name"] for p in projects}
        for i, f in enumerate(as_completed(futures), 1):
            name = futures[f]
            unplanned[name] = f.result()
            t = unplanned[name]["total"]
            if t > 0:
                print(f"  [{i}/{len(projects)}] {name}: {t} sin sprint")
            else:
                print(f"  [{i}/{len(projects)}] {name}: ok")

    print("Buscando menciones...")
    mentions = get_mentions()
    active_m = sum(1 for m in mentions if m["fields"]["system.state"] in
                   {"New","Active","Doing","In Progress","Blocked","Test","Estimate"})
    print(f"  {len(mentions)} menciones encontradas ({active_m} activas)")

    print("Generando HTML...")
    html = build_html(projects, stats, unplanned, mentions)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nDashboard generado: {out}")


if __name__ == "__main__":
    main()
