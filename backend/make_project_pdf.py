# -*- coding: utf-8 -*-
"""Generate PROJECT_EXPLANATION.pdf — a detailed explanation of the
Adaptive model-agnostic LLM gateway project. Pure fpdf2, no markdown parser.
"""
from fpdf import FPDF

OUT = r"c:\Users\Dell\Desktop\hackathon\llm-cost-optimizer\PROJECT_EXPLANATION.pdf"

_REPL = {
    "\u2014": "-", "\u2013": "-", "\u2192": "->", "\u2022": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u00a0": " ", "\u2265": ">=", "\u2264": "<=",
    "\u00d7": "x", "\u2713": "OK", "\u2705": "OK", "\u26a0": "!",
}


def _t(s):
    """Make text latin-1 safe for fpdf core fonts (helvetica/courier)."""
    s = str(s)
    for k, v in _REPL.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


# ---------------------------------------------------------------- helpers
class Doc(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("helvetica", "I", 8)
            self.set_text_color(120)
            self.cell(0, 6, _t("Adaptive — Model-Agnostic LLM Cost Optimizer"), align="L")
            self.cell(0, 6, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

    def footer(self):
        self.set_y(-14)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(150)
        self.cell(0, 8, _t("Generated 2026-09-06 — hackathon project documentation"), align="C")


def h1(pdf, text):
    pdf.set_font("helvetica", "B", 17)
    pdf.set_text_color(20, 40, 90)
    pdf.ln(2)
    pdf.multi_cell(0, 9, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(20, 40, 90)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)


def h2(pdf, text):
    pdf.set_font("helvetica", "B", 12.5)
    pdf.set_text_color(30, 60, 130)
    pdf.ln(2)
    pdf.multi_cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def body(pdf, text):
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(35)
    pdf.multi_cell(0, 5.2, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def bullets(pdf, items):
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(35)
    for it in items:
        pdf.set_x(pdf.l_margin + 2)
        pdf.multi_cell(0, 5.2, _t("•  " + it), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def code(pdf, text):
    pdf.set_font("courier", "", 8.6)
    pdf.set_text_color(30)
    pdf.set_fill_color(243, 244, 246)
    pdf.set_draw_color(210)
    pdf.multi_cell(0, 4.6, _t(text), border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def table(pdf, headers, rows, widths):
    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(30, 60, 130)
    pdf.set_text_color(255)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6.5, _t(h), border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(35)
    for r in rows:
        # row height = max lines among cells
        maxlines = 1
        for cell, w in zip(r, widths):
            maxlines = max(maxlines, len(pdf.multi_cell(w - 2, 4.6, _t(cell), dry_run=True, output="LINES")))
        rh = 4.6 * maxlines + 2
        x0, y0 = pdf.l_margin, pdf.get_y()
        if y0 + rh > pdf.page_break_trigger:
            pdf.add_page()
            x0, y0 = pdf.l_margin, pdf.get_y()
        for cell, w in zip(r, widths):
            x = pdf.get_x()
            y = pdf.get_y()
            pdf.multi_cell(w, 4.6, _t(cell), border=1, new_x="RIGHT", new_y="TOP", max_line_height=4.6)
            pdf.set_xy(x + w, y)
        pdf.set_xy(x0, y0 + rh)
    pdf.ln(2)


# ---------------------------------------------------------------- document
pdf = Doc(format="A4")
pdf.set_margins(16, 16, 16)
pdf.set_auto_page_break(auto=True, margin=18)
pdf.add_page()

# ---- title block
pdf.set_fill_color(20, 40, 90)
pdf.set_text_color(255)
pdf.set_font("helvetica", "B", 21)
pdf.cell(0, 14, _t("Adaptive — Model-Agnostic LLM Cost Optimizer"), fill=True, new_x="LMARGIN", new_y="NEXT")
pdf.set_font("helvetica", "I", 11)
pdf.cell(0, 8, _t("Minimum capable intelligence for every request — profiled, routed, verified, measured."),
         fill=True, new_x="LMARGIN", new_y="NEXT")
pdf.ln(4)
pdf.set_text_color(35)

body(pdf, "Adaptive is a multi-tenant LLM gateway built with FastAPI (Python 3.10) and a React (Vite) "
          "control-center dashboard. Customers obtain a gateway API key, connect their OWN providers "
          "(base_url + API key + model_id), and route requests through the gateway with model=\"auto\". "
          "The system auto-profiles every connected model's capabilities (customers never label models "
          "cheap/mid/frontier), understands every request, selects the cheapest capable model from the "
          "customer's own pool, caches reusable context (namespaced per customer), verifies answer "
          "quality, escalates when necessary, and measures the actual cost/quality trade-off against an "
          "always-best-model baseline — net of control-plane overhead.")

code(pdf, "PROFILE -> UNDERSTAND -> FILTER -> OPTIMIZE -> GENERATE -> VERIFY -> ESCALATE -> MEASURE -> LEARN")

# ---- 1. problem
h1(pdf, "1. The Problem")
body(pdf, "Every request sent to the strongest (most expensive) model wastes money; every request sent "
          "to a weak model risks quality. Cost savings claimed without quality measurement are not "
          "optimization. Teams also waste time manually labeling models as cheap/mid/frontier, and "
          "AI-assisted routing often hides the cost of the routing AI itself inside the reported savings.")

# ---- 2. solution
h1(pdf, "2. The Solution")
bullets(pdf, [
    "Gateway tenancy — customers get a gw_ API key (encrypted, shown once) and register their own models "
    "(base_url + encrypted credential + model_id).",
    "Model Registry — customers connect any models; no cheap/frontier labels are ever required.",
    "Model Profiler — measures per-category capability scores on a 24-item test set, through the "
    "customer's own endpoint. Scores are benchmark performance on our set, never claimed as universal "
    "intelligence scores.",
    "Task Analyzer — transparent heuristics: task type, difficulty 0-1, confidence, required "
    "capabilities + thresholds.",
    "Hybrid Router — Nemotron (control plane) RECOMMENDS a model; a deterministic validator DECIDES "
    "(exists / owner / enabled / context fits / capabilities / pricing valid / not blocked), falling "
    "back to the deterministic router. Provenance is always reported: nemotron_recommended | "
    "deterministic | nemotron_rejected_fallback.",
    "Dynamic tiers — cheap/mid/frontier derived from the customer's own pool (price terciles), never "
    "hard-coded.",
    "Quality Evaluator — deterministic, zero extra LLM calls: 0.5*correctness + 0.3*relevance + "
    "0.2*completeness, labeled reference (grounded) or estimated (heuristic, never ground truth).",
    "Escalation — quality below threshold retries the next-best model (capped attempts, honest summed "
    "cost), preferring in-tier models and only reaching above-baseline-tier models as a last resort.",
    "Prompt cache — in-memory, namespaced per customer: exact-prompt hits skip the LLM (savings "
    "measured); same-context/new-question hits count avoided tokens (savings estimated). Never "
    "conflated. Tenant A can never hit tenant B's entries.",
    "Cost engine — actual spend + counterfactual always-best baseline (measured tokens x best-model "
    "pricing; no duplicate expensive calls). net_savings_usd = gross savings - control-plane overhead.",
    "Pricing integrity — declared prices win, then the curated catalog, then pricing_status \"unknown\" "
    "— prices are NEVER fabricated; unpriced models are excluded from cost-optimal routing (clean 400, "
    "not a guess).",
    "Token optimizer — free, deterministic: prompt normalization (code fences preserved), chars/4 token "
    "estimation, and predicted output budgets (128/256/512) so short answers don't pay for a 512-token "
    "allowance. Cache hits are checked BEFORE the LLM classifier.",
    "Benchmark Lab — 50 reference-scored queries across 10 categories; baseline quality measured on a "
    "deterministic n=5 sample. Plus a naive-vs-optimized token-efficiency benchmark.",
])

# ---- 3. pipeline
h1(pdf, "3. The Core Pipeline")
body(pdf, "Every request flows through nine stages. Cache is checked first (before any LLM or "
          "control-plane call), so a hit avoids even the classifier call.")

code(pdf,
     "Request (model=auto)\n"
     "   |\n"
     "   v\n"
     "[1] Cache check  exact -> semantic -> context      (hit = $0 spend, MEASURED savings)\n"
     "   | miss\n"
     "   v\n"
     "[2] Task Analyzer / Nemotron classifier            (type, difficulty, capabilities)\n"
     "   v\n"
     "[3] Hybrid Router   Nemotron RECOMMENDS -> deterministic validator DECIDES\n"
     "   |                                                (owner/enabled/context/caps/pricing)\n"
     "   v\n"
     "[4] Token optimizer  normalization + output budget (free, deterministic)\n"
     "   v\n"
     "[5] GENERATE via the CUSTOMER's own endpoint       (their base_url + their key)\n"
     "   v\n"
     "[6] VERIFY quality  0.5*correct + 0.3*relevance + 0.2*completeness\n"
     "   | fail\n"
     "   v\n"
     "[7] ESCALATE to next-best model (capped attempts, summed cost)\n"
     "   v\n"
     "[8] MEASURE  actual vs always-best baseline, net of control-plane cost\n"
     "   v\n"
     "[9] LEARN  routing stats + request history -> dashboard; re-profile to refresh")

# ---- 4. architecture
h1(pdf, "4. Architecture")
code(pdf,
     "React (Vite) --REST--> FastAPI --> Optimizer --+--> Cache check\n"
     "                                               +--> Control plane (Nemotron via OpenCode Zen)\n"
     "                                               |      +- classifier (task type/difficulty)\n"
     "                                               |      +- cache verifier (veto-only)\n"
     "                                               |      +- LLM evaluator (subjective only)\n"
     "                                               +--> Task Analyzer -> Hybrid Router\n"
     "                                               +--> Customer's provider endpoint\n"
     "                                               +--> Quality -> Escalate\n"
     "                                               +--> Cost + baseline engine\n"
     "                                               +--> MongoDB (Atlas) / memory fallback")

h2(pdf, "Backend layout (backend/)")
bullets(pdf, [
    "core/ — optimizer, router, cache, quality, profiler, registries, cost engine, token optimizer.",
    "api/ — v1 gateway (multi-tenant) + platform control-center endpoints.",
    "providers/ — adapter hierarchy: BaseLLMProvider -> OpenAICompatibleProvider | OpenAIProvider | "
    "CustomProvider.",
    "llm/ — control plane: classifier, cache verifier, LLM evaluator, per-request cost ledger.",
    "database/ — MongoDB (Atlas) analytics store with in-memory fallback.",
    "benchmark/ — 50-query reference-scored dataset + runner + token-efficiency benchmark.",
    "data/ — pricing registry, model profiles, capability tests (JSON, single-writer MVP).",
])
h2(pdf, "Frontend layout (frontend/)")
bullets(pdf, [
    "Command Center — KPIs, cost comparison, request flow, model distribution, quality gauge.",
    "Models — registry table with pricing status, capability source, profile status.",
    "Playground — run the full pipeline with a prompt, context, and reference answer.",
    "Cache — hit rates, avoided tokens, measured vs estimated savings.",
    "Benchmark Lab — run benchmarks, mode selector, latest stored result.",
])

# ---- 5. gateway tenancy
h1(pdf, "5. Gateway Tenancy (Multi-Tenant)")
bullets(pdf, [
    "POST /v1/customers provisions a customer and returns a gw_... API key exactly once (XOR-encrypted "
    "at rest, never logged or echoed back).",
    "Every /v1 request authenticates with Authorization: Bearer gw_...; missing/invalid keys get clean "
    "401s.",
    "Tenant isolation is verified live: customer B sees 0 models, gets 404s on A's models, and 0 "
    "analytics rows.",
    "Customer credentials are decrypted only per-call, inside the provider factory; the platform's "
    "OpenCode key is never used for customer traffic.",
])

# ---- 6. hybrid routing
h1(pdf, "6. Hybrid Routing — AI Recommends, Code Decides")
body(pdf, "The control plane uses Nemotron 3.5 Lightning Free (default model id "
          "nemotron-3.5-lightning-free, env-configurable via OPENCODE_MODEL) through OpenCode Zen. It "
          "never decides alone:")
bullets(pdf, [
    "1. RECOMMEND — Nemotron sees the task analysis and the customer's candidate pool and proposes a "
    "model.",
    "2. VALIDATE — the deterministic router checks: model exists, belongs to this customer, is enabled, "
    "context fits the window, required capabilities are met, pricing is valid, model not blocked.",
    "3. FALLBACK — any failed check falls back to the deterministic router; provenance "
    "(nemotron_recommended | deterministic | nemotron_rejected_fallback) is always reported in the "
    "response.",
])
body(pdf, "Control-plane jobs: classifier (task type/difficulty as strict JSON, falls back to the legacy "
          "analyzer on any failure), cache verifier (veto-only — it can never approve a reuse the "
          "deterministic gates blocked), and LLM evaluator (subjective tasks only, labeled llm_judge). "
          "Every control-plane token is priced in a per-request ledger.")

# ---- 7. cache
h1(pdf, "7. Cache — Namespaced Per Customer")
table(pdf,
      ["Tier", "Match", "Savings label", "Notes"],
      [["Exact", "SHA-256 of normalized prompt", "MEASURED", "$0 spend; skips LLM and classifier"],
       ["Semantic", "canonicalized char-3gram cosine + safety gates", "MEASURED", "veto-only LLM verifier may block reuse"],
       ["Context", "same context, new question", "ESTIMATED", "counts avoided input tokens"]],
      [22, 62, 30, 66])
body(pdf, "Only quality-passed answers are stored. Tenant A can never hit tenant B's entries "
          "(namespace = customer_id). Cache is checked BEFORE the LLM classifier, so a hit avoids even "
          "the control-plane call (classifier_calls_avoided_exact/semantic).")

# ---- 8. cost accounting
h1(pdf, "8. Honest Cost Accounting")
table(pdf,
      ["Field", "Meaning"],
      [["actual_cost_usd", "What was really spent (summed across escalation attempts)"],
       ["baseline_cost_usd", "What the always-best model would have cost (measured tokens x best-model price)"],
       ["savings_usd", "baseline - actual (gross savings)"],
       ["control_plane_cost_usd", "What the AI routing itself cost (classifier/verifier/evaluator tokens)"],
       ["net_savings_usd", "gross savings - control-plane overhead; AI never hides its own cost"]],
      [46, 134])
body(pdf, "Usage is provider-reported when available, otherwise estimated at chars/4 and flagged "
          "usage_estimated — never fabricated. Pricing resolution: customer-declared -> curated catalog "
          "-> pricing_status \"unknown\" (excluded from cost-optimal routing with a clean 400).")

# ---- 9. api
h1(pdf, "9. API Surface")
h2(pdf, "Gateway API (/v1, multi-tenant — auth: Authorization: Bearer gw_...)")
table(pdf,
      ["Method", "Path", "Notes"],
      [["POST", "/v1/customers", "provision customer; gw_ key returned exactly once"],
       ["GET", "/v1/customers/me", "whoami"],
       ["POST", "/v1/models/register", "connect the customer's own model (base_url + key + prices)"],
       ["GET", "/v1/models", "my models (?enabled_only=true)"],
       ["GET/PUT/DELETE", "/v1/models/{id}", "read / update (rotate key, reprice) / delete"],
       ["POST", "/v1/models/{id}/test", "live connectivity test through the customer's endpoint"],
       ["POST", "/v1/models/{id}/profile", "auto-profile capability (background job)"],
       ["POST", "/v1/optimize", "full pipeline over the customer's pool"],
       ["POST", "/v1/chat/completions", "OpenAI-compatible; model=\"auto\" = gateway routes"],
       ["GET", "/v1/analytics", "customer-scoped analytics + routing stats"],
       ["GET", "/v1/health", "gateway health for the caller"]],
      [26, 52, 102])
h2(pdf, "Platform API (/api, single-tenant control center)")
bullets(pdf, [
    "/health, /api/chat (full pipeline), /api/route/preview (free dry-run, no LLM call).",
    "/api/models CRUD + /api/models/profiles + profiling jobs (start + poll progress).",
    "/api/models/control-plane — CP config, budgets, health, lifetime stats.",
    "/api/analytics, /api/routing-stats, /api/requests/{id}, /api/cache/stats, /api/cache/clear.",
    "/api/benchmark/queries | run | jobs/{id} | latest; /api/benchmark/token-efficiency (+jobs).",
    "/api/test/generate — single-model smoke test.",
])

# ---- 10. verification
h1(pdf, "10. Verification Status (All Green)")
table(pdf,
      ["Suite", "Result"],
      [["11 unit/integration suites (registry, control-plane, router, quality, cache+cost, profiler, "
        "token-optimizer, benchmark, audit-fixes, gateway-core, v1-api)", "ALL exit 0"],
       ["79-check offline /v1 API suite (test_v1_api.py, no network)", "PASS"],
       ["106-check LIVE smoke (real OpenCode generation: live ping, 24-test profiling job, optimize "
        "quality 0.9, exact cache hit, forced-model + escalation, tenant isolation, net-savings "
        "analytics)", "106/106 PASS"]],
      [136, 44])

# ---- 11. quickstart
h1(pdf, "11. Quickstart")
code(pdf,
     "cd llm-cost-optimizer\n"
     "Copy-Item .env.example .env    # set OPENCODE_API_KEY (https://opencode.ai/auth)\n"
     "pip install -r backend\\requirements.txt\n"
     "uvicorn backend.main:app --reload --port 8000\n"
     "\n"
     "# Frontend\n"
     "cd frontend; npm install; npm run dev   # http://localhost:5173 (proxies /api -> :8000)")

h2(pdf, "Gateway in 60 seconds (PowerShell)")
code(pdf,
     "$r = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/customers `\n"
     "     -ContentType \"application/json\" -Body '{\"customer_id\":\"acme\",\"name\":\"Acme Corp\"}'\n"
     "$H = @{ Authorization = \"Bearer $($r.api_key)\" }   # shown ONCE - store it\n"
     "\n"
     "Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/register -Headers $H `\n"
     "  -ContentType \"application/json\" -Body (@{ model_id=\"acme-mini\"\n"
     "    base_url=\"https://api.acme.ai/v1\"; api_key=\"sk-acme-1\"\n"
     "    input_per_1M=0.12; output_per_1M=0.48 } | ConvertTo-Json)\n"
     "\n"
     "Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/chat/completions `\n"
     "  -Headers $H -ContentType \"application/json\" -Body (@{ model=\"auto\"\n"
     "    messages=@(@{role=\"user\"; content=\"What is an API?\"}) } | ConvertTo-Json -Depth 5)")

# ---- 12. limitations
h1(pdf, "12. Honest Limitations")
bullets(pdf, [
    "Quality scoring is lexical, not semantic; the strict 0.75 bar drives high escalation on terse "
    "answers.",
    "Capability profiles use 4 samples/category — noisy; the UI labels measured vs estimated "
    "everywhere.",
    "In-memory cache + JSON registries are single-process (fine for a hackathon, not multi-replica "
    "prod).",
    "Customer-supplied base_url is an accepted SSRF surface for the MVP (documented in SECURITY.md); "
    "production needs allow-lists/egress controls.",
    "Credential encryption is XOR-keystream obfuscation, not KMS-grade.",
    "No per-customer rate limits/quotas yet.",
    "Baseline quality from an n=5 sample — reported with n, not hidden.",
    "The LLM evaluator is a judge, not ground truth; subjective scores are labeled llm_judge.",
])

# ---- 13. future
h1(pdf, "13. Future Improvements")
bullets(pdf, [
    "Semantic (embedding) similarity for the cache tier.",
    "More profiler items per category; scheduled re-profiling from live outcomes (LEARN step is "
    "currently manual re-run).",
    "Provider-side prompt caching wired to measured cache-read billing.",
    "Per-model latency tracking in routing; budget-constrained routing mode.",
    "Per-customer rate limits, quotas, and key rotation endpoints.",
    "KMS-backed credential storage; audit log for security events.",
    "Aggregate control-plane spend in analytics (currently per-request ledger + benchmark totals).",
])

# ---- 14. docs
h1(pdf, "14. Project Documentation")
table(pdf,
      ["File", "Contents"],
      [["API.md", "Full /v1 + /api endpoint reference with request/response shapes"],
       ["MODEL_REGISTRY.md", "Registration, pricing statuses, profiling, capability sources"],
       ["ROUTING.md", "Hybrid router: recommendation, validation gates, fallback, provenance"],
       ["SECURITY.md", "Key handling, credential encryption, SSRF caveat, threat model"],
       ["DEMO_GUIDE.md", "Step-by-step live demo walkthrough for judges"],
       ["ARCHITECTURE.md", "Component diagram, data flow, module responsibilities"],
       ["README.md", "Overview, setup, env vars, endpoint tables, methodology"]],
      [40, 140])

pdf.output(OUT)
print("WROTE", OUT)
