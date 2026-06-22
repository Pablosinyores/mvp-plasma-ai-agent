"""Minimal observability dashboard (M3) — FastAPI + a single HTMX page on :8080.

Read-only. Lists agents (DynamoDB), their on-chain balances (Anvil), recent jobs (Commerce), and a
live spend/event feed (the EventLog). The page self-refreshes a fragment via HTMX every few seconds —
no build step, no JS framework. Port 8080 (the model server is on 8081).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.aws import Aws  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402
from plasma_mvp.events import EventLog  # noqa: E402
from plasma_mvp.registry import Registry  # noqa: E402

app = FastAPI(title="MVP Plasma Agent Dashboard")

_cfg = load_config()


def _ctx():
    aws = Aws(_cfg)
    return {
        "adapter": LocalAdapter(_cfg),
        "registry": Registry(aws, _cfg),
        "events": EventLog(aws, _cfg),
    }


def _usdt(x):
    return "{:.6f}".format(x / 1e6)


PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8"><title>Plasma Agent Studio — Dashboard</title>
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<style>
 body{{font-family:ui-monospace,Menlo,monospace;background:#0b0f17;color:#d7e0ea;margin:0;padding:24px}}
 h1{{font-size:18px;color:#7ee787}} h2{{font-size:14px;color:#79c0ff;margin:18px 0 6px}}
 table{{border-collapse:collapse;width:100%;font-size:12px}}
 th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #1d2633}}
 th{{color:#8b98a9;font-weight:600}} .mono{{color:#adbac7}}
 .pill{{padding:1px 7px;border-radius:9px;font-size:11px}}
 .spend{{background:#3b1f24;color:#ff7b72}} .refuel{{background:#1f3b2a;color:#7ee787}}
 .muted{{color:#6b7785}}
</style></head>
<body>
 <h1>⬡ MVP Plasma AI Agent — live dashboard</h1>
 <div class="muted">chain {chain} · auto-refresh 3s</div>
 <div hx-get="/panel" hx-trigger="load, every 3s" hx-swap="innerHTML">loading…</div>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE.format(chain=_cfg.chain_id)


@app.get("/panel", response_class=HTMLResponse)
def panel():
    try:
        c = _ctx()
        return _render_panel(c)
    except Exception as e:  # noqa: BLE001
        return "<p class='muted'>stack unavailable: {}</p>".format(e)


def _render_panel(c) -> str:
    adapter, registry, events = c["adapter"], c["registry"], c["events"]

    rows = []
    for a in sorted(registry.list_agents(), key=lambda r: r["name"]):
        eth = adapter.eth_balance(a["address"]) / 1e18
        usdt = adapter.usdt_balance(a["address"])
        rows.append(
            "<tr><td>{}</td><td>{}</td><td class='mono'>{}</td><td>{:.4f}</td><td>{}</td></tr>".format(
                a["name"], a["agentId"], a["address"], eth, _usdt(usdt)
            )
        )
    agents_tbl = (
        "<table><tr><th>agent</th><th>id</th><th>address</th><th>ETH</th><th>USDT</th></tr>"
        + ("".join(rows) or "<tr><td colspan=5 class='muted'>no agents</td></tr>")
        + "</table>"
    )

    job_rows = []
    for jid in range(max(1, adapter.job_count() - 9), adapter.job_count() + 1):
        try:
            j = adapter.get_job(jid)
        except Exception:  # noqa: BLE001
            continue
        job_rows.append(
            "<tr><td>{}</td><td>{}</td><td class='mono'>{}</td><td>{}</td></tr>".format(
                j["jobId"], j["status"], j["provider"], _usdt(j["budget"])
            )
        )
    jobs_tbl = (
        "<table><tr><th>job</th><th>status</th><th>provider</th><th>budget</th></tr>"
        + ("".join(reversed(job_rows)) or "<tr><td colspan=4 class='muted'>no jobs</td></tr>")
        + "</table>"
    )

    feed = []
    for e in events.list(limit=20):
        d = e["data"]
        cls = "refuel" if e["kind"] == "refuel" else "spend"
        amt = _usdt(int(d.get("amount", 0)))
        detail = "{} → {}".format(d.get("payer", d.get("owner", "?"))[:10],
                                  d.get("payee", d.get("agent", "?"))[:10])
        feed.append(
            "<tr><td><span class='pill {}'>{}</span></td><td>{} USDT</td>"
            "<td class='mono'>{}</td></tr>".format(cls, e["kind"], amt, detail)
        )
    feed_tbl = (
        "<table><tr><th>event</th><th>amount</th><th>flow</th></tr>"
        + ("".join(feed) or "<tr><td colspan=3 class='muted'>no spend yet</td></tr>")
        + "</table>"
    )

    return (
        "<h2>agents</h2>" + agents_tbl
        + "<h2>jobs (recent)</h2>" + jobs_tbl
        + "<h2>spend / refuel feed</h2>" + feed_tbl
    )
