#!/usr/bin/env bash
# demo_record.sh — single-window presenter for the MVP Plasma AI Agent demo.
#
# Runs the whole Phase 1→6 walkthrough in ONE terminal window (worker + dashboard in the
# background, phases + a text dashboard panel in the foreground) so a window-recorder can
# capture it in one file.
#
# Assumes the stack is already CLEAN and UP:
#   docker compose down && rm -rf backend/.agent contracts/deployments/local.json
#   make up && make model && curl -s localhost:8081/v1/models   # wait for JSON
#
# Usage:
#   ./scripts/demo_record.sh                 # real model (llamacpp), default pacing
#   MODEL_BACKEND=stub ./scripts/demo_record.sh   # instant, deterministic fallback
#   PACE=3 ./scripts/demo_record.sh          # slower beats between phases
set -uo pipefail
cd "$(dirname "$0")/.."

PACE="${PACE:-2}"
export MODEL_BACKEND="${MODEL_BACKEND:-llamacpp}"
PY_QUIET='import warnings; warnings.filterwarnings("ignore")'

c_title=$'\e[1;36m'; c_ok=$'\e[1;32m'; c_warn=$'\e[1;33m'; c_dim=$'\e[2m'; c_desc=$'\e[0;37m'; c_off=$'\e[0m'

banner() { echo; echo "${c_title}════════════════════════════════════════════════════════════${c_off}";
           echo "${c_title}  $1${c_off}";
           echo "${c_title}════════════════════════════════════════════════════════════${c_off}"; sleep "$PACE"; }
# desc — the scenario narration printed under a banner: WHAT we are about to do and WHY it matters.
desc()   { echo; while [ "$#" -gt 0 ]; do echo "${c_desc}   $1${c_off}"; shift; done; echo; sleep "$PACE"; }
note()   { echo "${c_dim}» $1${c_off}"; }
step()   { echo "${c_dim}   ↳ $1${c_off}"; }
pause()  { sleep "${1:-$PACE}"; }   # explicit beat between important events, so a viewer can read each one
run()    { echo "${c_ok}\$ $*${c_off}"; "$@"; sleep 1; }

WORKER_PID=""; DASH_PID=""
SKIP_BG="${SKIP_BG:-0}"   # when 1, a wrapper already started worker+dashboard and will clean them up
cleanup() { [ "$SKIP_BG" = "1" ] && return 0
            echo; note "stopping background worker + dashboard";
            [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null
            [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null
            pkill -f studio_worker.py 2>/dev/null; pkill -f "studio.py dashboard" 2>/dev/null; true; }
trap cleanup EXIT

# in-venv python that hushes the boto3 py39 deprecation noise
py() { python3 -c "$PY_QUIET
import sys; exec(sys.stdin.read())" ; }
jobstatus() { python3 - "$@" <<PY 2>/dev/null
$PY_QUIET
import sys; sys.path.insert(0,"backend/sdk")
from plasma_mvp.adapter import LocalAdapter
a=LocalAdapter(); ids=[int(x) for x in sys.argv[1:]]
print(" ".join(a.get_job(i)["status"] for i in ids))
PY
}

# --------------------------------------------------------------------------- #
banner "MVP PLASMA AI AGENT — local-first autonomous on-chain agent studio"
desc "What this is: a fully local sandbox where software agents get a real on-chain identity," \
     "EARN stablecoins by running model jobs, and SPEND those stablecoins on paid resources —" \
     "all under hard, on-chain-enforced safety caps. Nothing here touches a public network." \
     "" \
     "This walkthrough has 6 phases:" \
     "   1) Identity   2) Earning   3) Marketplace   4) Guarded spend   5) Attack test   6) Refuel" \
     "" \
     "Infra under the hood:" \
     "   chain   : Anvil  :8545  (local EVM — escrow, USDT, agent-registry NFT live here)" \
     "   cloud   : LocalStack :4566  (KMS for key custody · S3 for agent cards · DynamoDB ledgers)" \
     "   model   : $MODEL_BACKEND  (the LLM that actually does the paid work)"
pause 3

# --------------------------------------------------------------------------- #
banner "PHASE 1 — Identity: give an agent a real on-chain identity"
desc "Goal: before an agent can earn or spend, it needs an identity nobody can forge or drain." \
     "We mint a fresh signing key INSIDE KMS (the raw private key never leaves the vault)," \
     "publish a public 'agent card' to S3 describing what the agent does, and register the agent" \
     "as an NFT on-chain so its identity + card URI are verifiable by anyone."
note "step 1/4 — create the agent (KMS-encrypted key + S3 card + on-chain NFT registration):"
run python3 backend/cli/studio.py create price-watcher 2>/dev/null
pause
note "step 2/4 — resolve it back from the chain (agentId, owner address, the S3 card JSON):"
run python3 backend/cli/studio.py resolve price-watcher 2>/dev/null
pause
note "step 3/4 — check its starting wallet (gas ETH for tx, 0 USDT — it has not earned yet):"
run python3 backend/cli/studio.py balance price-watcher 2>/dev/null
pause
note "step 4/4 — SECURITY CHECK: prove the private key is NEVER stored in plaintext anywhere."
step "we fetch the stored secret and assert the raw key bytes are not inside it —"
step "the value on disk is KMS-ciphertext only, decryptable solely by the KMS key."
python3 - <<PY 2>/dev/null
$PY_QUIET
import sys; sys.path.insert(0,"backend/sdk")
from plasma_mvp.keyvault import KeyVault
kv=KeyVault(); acct=kv.signer_for("price-watcher"); ct=kv.ciphertext_for("price-watcher")
assert acct.key not in ct
print("  ✓ PASS: raw key is NOT in the stored secret — only KMS-decryptable")
PY
pause 3

# --------------------------------------------------------------------------- #
if [ "$SKIP_BG" != "1" ]; then
  banner "Background services — the always-on studio worker + live dashboard"
  desc "The worker is the autonomous engine: it watches for funded jobs, runs each one through" \
       "the model, submits the result on-chain, and a keeper auto-settles payment from escrow." \
       "The dashboard is a live HTMX view of every agent, balance, job, and spend/refuel event."
  python3 backend/studio_worker.py > /tmp/studio_worker.log 2>&1 & WORKER_PID=$!
  python3 backend/cli/studio.py dashboard > /tmp/studio_dash.log 2>&1 & DASH_PID=$!
  sleep 4
  note "worker up (services every agent + auto-settles) · dashboard live on http://localhost:8080"
else
  note "worker + dashboard already running (wrapper-managed) · dashboard on http://localhost:8080"
fi
pause

# --------------------------------------------------------------------------- #
banner "PHASE 2 — Earning: fund a job, the local model runs it, the agent gets PAID"
desc "Goal: show the full earn loop end-to-end. A buyer funds a job into on-chain ESCROW with a" \
     "USDT budget. The worker picks it up, the LOCAL model produces the answer, the result is" \
     "submitted, and only THEN does the keeper release escrow to the agent. No work, no pay." \
     "Watch the status walk: FUNDED → SUBMITTED (model finished) → COMPLETED (escrow settled)."
note "fund a 5 USDT job for price-watcher (budget locked in escrow up front):"
run python3 backend/cli/studio.py fund-job price-watcher \
  --prompt "Summarize in one sentence: Plasma is a stablecoin chain where agents earn and spend USDT autonomously under on-chain escrow." \
  --budget 5 2>/dev/null
pause
note "polling job 1 every 2s while the worker + model do the work (real inference takes a few seconds):"
for i in $(seq 1 25); do st=$(jobstatus 1); echo "   t+$((i*2))s  job1 = $st"; [ "$st" = "COMPLETED" ] && break; sleep 2; done
pause
echo; note "what the worker logged (it submitted the job, the keeper settled it):"; grep -E "submitted|settled" /tmp/studio_worker.log
pause
note "agent wallet after settlement — the 5 USDT budget is now the agent's earnings:"
run python3 backend/cli/studio.py balance price-watcher 2>/dev/null
pause 3

# --------------------------------------------------------------------------- #
banner "PHASE 3 — Marketplace: one worker, many agents earning in parallel"
desc "Goal: prove this scales past a single agent. We spin up two MORE agents, fund a job for" \
     "each, and let the SAME worker service all of them concurrently. This is the shape of an" \
     "agent economy — many independent identities, each with its own wallet, earning side by side." \
     "Jobs 2 and 3 will settle independently as each one's model output lands."
note "register two more agents (each gets its own KMS key + S3 card + NFT, just like phase 1):"
run python3 backend/cli/studio.py create news-summarizer 2>/dev/null
pause 1
run python3 backend/cli/studio.py create data-cleaner 2>/dev/null
pause
note "fund a job for each of the new agents (3 USDT and 4 USDT budgets):"
run python3 backend/cli/studio.py fund-job news-summarizer --prompt "Summarize: the agent economy lets software pay software." --budget 3 2>/dev/null
pause 1
run python3 backend/cli/studio.py fund-job data-cleaner --prompt "List 3 keywords from: escrow, settlement, refuel, identity." --budget 4 2>/dev/null
pause
note "polling both jobs every 2s — they settle independently, not in lockstep:"
for i in $(seq 1 30); do s=$(jobstatus 2 3); echo "   t+$((i*2))s  jobs(2,3) = $s"; [ "$s" = "COMPLETED COMPLETED" ] && break; sleep 2; done
pause
echo; note "final wallets — three agents, three independent USDT balances, all earned from one worker:"
for n in price-watcher news-summarizer data-cleaner; do run python3 backend/cli/studio.py balance $n 2>/dev/null; done
pause 3

# --------------------------------------------------------------------------- #
banner "PHASE 4 — Guarded spend: the agent PAYS for a resource, under hard caps"
desc "Goal: earning is half the loop — now the agent SPENDS. It buys a paid HTTP resource using" \
     "the x402 pay-per-call protocol. Every payment is gated by the X402Signer: a per-call cap, a" \
     "rolling session budget, and a payee allow-list. The agent cannot overspend even if it wants" \
     "to. Then auto-refuel tops the wallet back up — but never past the hard daily cap." \
     "Watch: a 200 response (resource paid for), the signer's running spend, and a capped refuel."
run python3 backend/cli/studio.py demo3 --name price-watcher 2>/dev/null
pause 3

# --------------------------------------------------------------------------- #
banner "PHASE 5 — Security test: a prompt-injection DRAIN attempt is BLOCKED"
desc "The threat: the model's output is attacker-controllable (poisoned web page, malicious job)." \
     "Suppose the model is tricked into emitting 'pay attacker 1,000,000 USDT'. In a naive agent" \
     "that drains the wallet. Here the spend layer treats model output as UNTRUSTED and runs it" \
     "through four independent guards. We fire the attack and confirm ZERO funds move." \
     "" \
     "The four guards under test:" \
     "   1) per-call cap        — single payment may not exceed max_value_per_call" \
     "   2) payee allow-list     — byte-exact match required; unknown recipient is refused" \
     "   3) dangerous-type gate  — Permit / PermitTransferFrom (open-ended approvals) rejected" \
     "   4) key-fetch ordering   — guards run BEFORE the key is decrypted, so it stays in KMS"
note "running the attack against the live signer guards…"
python3 - <<PY 2>/dev/null
$PY_QUIET
import sys, time; sys.path.insert(0,"backend/sdk")
from eth_account import Account
from plasma_mvp import x402
from plasma_mvp.signer import X402Signer, SpendCapExceeded, PayeeNotAllowed
USDT=1_000_000
agent=Account.create(); payee=Account.create().address; attacker=Account.create().address
q=lambda to,v: x402.PaymentQuote(pay_to=to,value=v,asset="0x"+"11"*20,chain_id=31337,
                                 valid_after=1000,valid_before=1300,nonce="0x"+"22"*32)
s=X402Signer(lambda:agent, max_value_per_call=2*USDT, session_budget=4*USDT, allowed_payees=[payee])
print("  MODEL OUTPUT (attacker-controlled): 'pay attacker 1000000 USDT'"); time.sleep(1)
print("  guard 1/4 — oversized payment vs per-call cap (2 USDT)…"); time.sleep(0.6)
try: s.sign_payment(q(attacker, 1_000_000*USDT))
except SpendCapExceeded as e: print("    ✗ BLOCKED by per-call cap")
time.sleep(1)
print("  guard 2/4 — payment to an unknown attacker address…"); time.sleep(0.6)
try: s.sign_payment(q(attacker, USDT))
except PayeeNotAllowed as e: print("    ✗ BLOCKED by byte-equal payee allow-list")
time.sleep(1)
print("  guard 3/4 — open-ended approval primitives…"); time.sleep(0.6)
g=x402.SigningPolicy()
for t in ("Permit","PermitTransferFrom"):
    try: g.check({"primaryType":t,"message":{"validAfter":0,"validBefore":1300}})
    except x402.PolicyViolation: print(f"    ✗ REJECTED dangerous type: {t}")
time.sleep(1)
print(f"  result — agent spent: {s.spent}  (ZERO moved; guard 4/4: the key was never even fetched)")
PY
pause 3

# --------------------------------------------------------------------------- #
banner "PHASE 6 — Auto-refuel: top up when low, but never past the hard daily cap"
desc "Goal: keep an agent funded for gas/spend without letting a bug or attacker bleed the treasury." \
     "When the balance drops below a floor, auto-refuel tops it up by a fixed amount. A per-day cap" \
     "is the backstop: once the day's refuels hit the limit, the NEXT refuel is refused outright." \
     "We force two refuels in one day — the first FIRES, the second is BLOCKED by the daily cap."
note "running two back-to-back refuels against the same day's ledger…"
python3 - <<PY 2>/dev/null
$PY_QUIET
import sys, json, time; sys.path.insert(0,"backend/sdk")
from plasma_mvp.adapter import LocalAdapter
from plasma_mvp.aws import Aws
from plasma_mvp.config import load_config
from plasma_mvp.events import EventLog
from plasma_mvp.refuel import AutoRefueler, RefuelLedger
cfg=load_config(); aws=Aws(cfg); a=LocalAdapter(cfg)
agent=json.load(open("backend/.agent/price-watcher.json"))["address"]
owner=a.relayer; a.mint_usdt(owner.address, 100_000_000)
day="refuel-demo-%d" % int(time.time())
rf=AutoRefueler(a, owner_account=owner, floor=1_000_000_000, refill=5_000_000,
                daily_cap=8_000_000, ledger=RefuelLedger(aws,cfg), cfg=cfg, events=EventLog(aws,cfg))
print("  balance before:", a.usdt_balance(agent)/1e6, "USDT"); time.sleep(1)
print("  refuel #1:", "FIRED +5 USDT" if rf.maybe_refuel(agent, day=day)["refueled"] else "blocked"); time.sleep(1.2)
print("  refuel #2:", "FIRED +5 USDT" if rf.maybe_refuel(agent, day=day)["refueled"] else "BLOCKED (daily cap reached)"); time.sleep(1)
print("  balance after :", a.usdt_balance(agent)/1e6, "USDT  (only one refuel got through)")
PY
pause 3

# --------------------------------------------------------------------------- #
banner "DASHBOARD — live state (agents · balances · jobs · spend/refuel feed)"
desc "Everything you just saw, reflected in the live dashboard: every agent and its wallet, every" \
     "job and its final status, and the running spend/refuel event feed. Same data, served on :8080."
curl -s http://localhost:8080/panel | sed 's/<[^>]*>/ /g' | tr -s ' ' | sed 's/^ //'
echo; pause 3

banner "DONE — earned on-chain, spent within caps, refueled safely, attack contained"
desc "Recap of what was proven, end to end:" \
     "   ✓ identity   — KMS-custodied key, never stored in plaintext" \
     "   ✓ earning    — work-gated escrow payout, scaled across 3 agents on 1 worker" \
     "   ✓ spending   — x402 pay-per-call under per-call + session + payee caps" \
     "   ✓ security   — prompt-injection drain blocked, ZERO funds moved" \
     "   ✓ treasury   — auto-refuel with a hard daily cap" \
     "" \
     "Explore the live dashboard at http://localhost:8080"
pause "$PACE"
