# Improving Car-Imports-Advisor: A Step-by-Step Guide

**Target audience:** recruiters and peers in the fullstack Data Science & Data Engineering space
**Repo reviewed:** [MghongoDev/Car-Imports-Advisor](https://github.com/MghongoDev/Car-Imports-Advisor)
**Track:** Data Engineering-leaning, with a clear Data Science layer on top — the goal is to show both halves of the DS&DE spectrum in one coherent system, not two disconnected demos.
**Stack decision:** Streamlit is phased out entirely. The backend is **FastAPI**, serving both the JSON API and a simple server-rendered frontend UI, deployed as one service. Database is **Neon** (Postgres free tier), no alternatives to weigh. No separate static/marketing site.

## Where the project stands today

The current repo is a good idea with a thin implementation:

- `app.py` is a single-file Streamlit UI that triggers extraction, training, and prediction from sidebar buttons.
- `src/extraction.py` contains five scraper functions (`scrape_sbt_japan`, `scrape_beforward`, `scrape_car_from_japan`, `scrape_aaajapan`, `scrape_japanese_car_trade`) that guess at CSS class names (`'car-card'`, `'listing-card'`, `'vehicle-listing'`, etc.) and, on any failure or empty result, **silently call `generate_mock_data()`** — so the app always "works" even when it is producing fabricated numbers. This is the single biggest credibility risk in the repo: a reviewer who reads the source will notice it in under a minute.
- `src/ml_model.py` trains a single `RandomForestRegressor` inside a `Pipeline` with `OneHotEncoder`, with no experiment tracking, no versioning, and no evaluation beyond a printed MAE/R².
- `src/calculator.py` hardcodes a JPY→KES exchange rate and duty percentages with no source citation or update path.
- `src/db_utils.py` uses local SQLite with no schema versioning or historical tracking — every "refresh" presumably overwrites rather than accumulates.
- There is no CDC, no scheduling, no tests, no CI, and no deployment.

None of this is a criticism of the idea — it's a reasonable starting scaffold. The rest of this guide turns it into something you'd defend in an interview.

## Guiding principle

Every phase below is ordered so that **each step is independently demo-able and commit-able**. Do not try to build the whole thing in one branch — a reviewer's GitHub history is part of your portfolio, and a clean sequence of meaningful commits (or PRs) tells its own story about how you work.

---

## Phase 0 — Stop the bleeding (do this before anything else)

Before adding anything new, remove the thing most likely to sink your credibility.

1. **Delete the silent mock-data fallback from the "real" scrapers.** If a scraper fails, it should raise or log a structured error and return an empty result — never quietly substitute fake data without saying so loudly in the logs and API response. Portfolio reviewers who spot silent fallbacks read them as "this project fakes its results," which undermines everything else in the repo.
2. **Keep `generate_mock_data()`, but rename its role explicitly** — e.g. move it into `src/mock_data.py` and only ever call it from a `--seed-demo` CLI flag or an explicit `POST /admin/seed-demo-data` endpoint, never as an automatic fallback inside a scraper.
3. **Commit this alone**, with a message like `fix: remove silent mock-data fallback in scrapers`. This single commit, visible in your git history, is a good signal on its own — it shows you can identify and fix a correctness/trust bug in your own code.

---

## Phase 1 — Real data engineering: scraping

### Step 1.1 — Pick and scope your source(s)

- Build the primary scraper against **jiji.co.ke** (cars category). It's the right choice: large local listings volume, consistent-ish HTML structure, and directly matches your "local price" comparison use case.
- Before writing a single selector, **check jiji.co.ke's `robots.txt` and Terms of Service yourself** at scrape time (rules change) and decide your crawl scope accordingly — which paths you'll fetch, how deep you'll paginate, and what request rate you'll use. Document this decision directly in the README's data section; a reviewer in this field will specifically look for evidence that you thought about this rather than ignored it.
- **Cheki.co.ke and PigiaMe are worth adding later, not now.** Adding a second source is exactly the kind of thing that demonstrates your scraper design generalizes (see Step 1.3), but shipping one working, well-engineered scraper beats three half-working ones. Treat a second source as a Phase 6 stretch goal.

### Step 1.2 — Build a resilient scraper

Replace the guess-and-fallback pattern with:

- **HTTP layer:** `requests` + `BeautifulSoup` is enough for jiji.co.ke if listings are server-rendered (verify this first — view source, not just dev tools, since dev tools show the post-JS DOM). If listings load via client-side JS/XHR, either hit the underlying JSON API directly (inspect Network tab for a `/api/` call — often simpler and more stable than parsing HTML) or use **Playwright**. Don't reach for Playwright by default — it's slower, heavier, and harder to schedule on a free-tier runner; only use it if there's no static HTML or API path.
- **Resilient parsing, not silent failure:** wrap each field extraction in its own try/except, log which field failed and for which listing URL, and mark the record with a `parse_warnings` field rather than dropping it or inventing a default. A row with `mileage_km: null, parse_warnings: ["mileage not found"]` is honest data engineering; a row with a fabricated `32000` is not.
- **Politeness:** a fixed delay or token-bucket rate limiter between requests, a realistic `User-Agent`, and retry-with-backoff (e.g. `tenacity` or a hand-rolled exponential backoff) on transient failures (timeouts, 429s, 5xxs) — but no retry on 403s that indicate you're blocked; back off entirely there.
- **Structured logging:** use Python's `logging` module (not `print`) with levels — `INFO` for run summaries ("scraped 340 listings, 12 parse warnings"), `WARNING` for per-field parse issues, `ERROR` for a fully failed fetch. This is a small change that reads as senior-level engineering to anyone reviewing the code.
- **Schema validation:** define a `pydantic` model (or `pandera` schema if you want DataFrame-level validation) for a car listing — types, required fields, plausible ranges (e.g. `year` between 1990–2027, `price_kes > 0`). Validate every scraped record against it before it reaches storage. This is your data quality gate, and — bonus — this same `pydantic` model becomes your FastAPI response schema in Phase 4, so you write it once and use it twice.

### Step 1.3 — Design for a second source (even if you only ship one)

Structure the scraper as a `BaseScraper` interface (`fetch()`, `parse_listing()`, `to_schema()`) with `JijiScraper` as the first concrete implementation. This costs you very little now and directly answers the "how would this scale to another site" interview question with working code instead of a hand-wave.

---

## Phase 2 — Real data engineering: change data capture (CDC)

There's no webhook from jiji.co.ke, so CDC here means **scheduled polling + diffing**, not event streaming. Design it like this:

1. **Stable listing identity:** extract or construct a stable `listing_id` (jiji listing URLs typically contain one — use that, not a hash of mutable fields).
2. **Content hash per listing:** compute a hash (e.g. `sha256`) over the fields you care about detecting changes in (price, mileage, description) each time you scrape a listing.
3. **Append-only price history table:** store every observed `(listing_id, scraped_at, price_kes, content_hash, ...)` row rather than overwriting. This is a minimal **SCD Type 2** pattern — cheap to implement, and it's exactly the vocabulary a DE-focused recruiter is listening for.
4. **Change detection logic on ingest:** for each newly scraped listing, compare its hash to the most recent stored hash for that `listing_id`. If different, insert a new history row and emit a `price_change` event (a simple row in a `price_events` table is enough — `listing_id, old_price, new_price, delta_pct, detected_at`). No need for Kafka/Debezium-style CDC infrastructure here; the polling-and-diff pattern is the honest, appropriately-scoped answer for a scraped-data source, and saying so explicitly in your README shows judgment rather than resume-driven over-engineering.
5. **Surface it through the API and UI:** a `GET /price-events` endpoint returning recently changed listings sorted by `delta_pct`, rendered as a "Price Movements" page in the frontend (Phase 4). This turns CDC from an invisible backend feature into something a recruiter can actually click through.

---

## Phase 3 — Orchestration and storage (staying Python-first)

| Layer | Recommendation | Why |
|---|---|---|
| Scheduling | **GitHub Actions cron** (`schedule:` trigger) calling a small Python script | Free, versioned alongside the code, no separate service to run or pay for. Prefect/Dagster are the "correct" production answer but are overkill for a single scheduled job — using them here reads as resume-padding to an experienced reviewer. Mention them in the README as "next step at scale" instead. |
| Storage (dev) | SQLite | Fine for quick local iteration before you wire up Neon. |
| Storage (deployed) | **Neon** (Postgres, free tier) | A single, genuinely persistent Postgres instance that both the FastAPI app (on Render) and the GitHub Actions scraper job can read/write, since they're two separate ephemeral environments that need to share state. Neon's serverless Postgres free tier is generous enough for a portfolio project's traffic and storage needs — confirm current limits before committing, since free-tier terms shift over time. |
| Validation | `pydantic` for the scraper's row schema, optionally `pandera` if you want to validate whole DataFrames before bulk insert | Both are lightweight and Python-native; pick one, don't add both. The `pydantic` models double as FastAPI request/response schemas (Phase 4). |
| Testing | `pytest` | Cover: calculator math (`calculator.py` is pure functions — easiest and highest-value place to start), schema validation rejecting bad rows, CDC diff logic with a fixed pair of before/after listings, and FastAPI endpoints via `httpx.AsyncClient`. |

Concrete file-level changes:

- `src/db_utils.py`: add a `listings_history` table (append-only, per Phase 2) alongside a `listings` "latest snapshot" table; add a lightweight migration approach (even a numbered `migrations/001_init.sql`, `002_add_history.sql` folder applied on startup is enough at this scale — no need for Alembic yet, though mentioning it as the natural next step is a good sentence for your README).
- `src/extraction.py`: split into `scrapers/base.py`, `scrapers/jiji.py`, and keep `mock_data.py` separate as established in Phase 0.
- New `src/cdc.py`: hashing, diffing, and `price_events` write logic from Phase 2.
- New `scripts/run_pipeline.py`: a CLI entrypoint (scrape → validate → store → detect changes) that both GitHub Actions and a local developer can call the same way — don't bury this logic inside route handlers, which can't be scheduled headlessly.

---

## Phase 4 — Backend and frontend: FastAPI, no Streamlit

Streamlit is fully replaced. One FastAPI application serves both the JSON API and the browser-facing UI, which also sidesteps the "two free services both cold-starting" problem you'd get from running a separate frontend framework alongside the API.

### Step 4.1 — API layer

- `POST /predict` — car specs in, predicted JPY price out (wraps `ml_model.py`)
- `POST /calculate` — predicted price + local price in, tax/cost breakdown out (wraps `calculator.py`)
- `GET /listings` and `GET /listings/{id}/history` — serves listings and per-listing price history from Neon
- `GET /price-events` — recent price drops/increases, sorted by `delta_pct`
- `POST /admin/trigger-scrape` — kicks off the pipeline on demand via `BackgroundTasks`, useful for live-demoing the CDC pipeline in an interview instead of waiting for the next cron run
- `GET /health` — last successful scrape timestamp, model version currently served
- Auto-generated OpenAPI docs at `/docs` come for free and are worth calling out in the README — a recruiter can exercise your model and your data endpoints without reading a line of code.

### Step 4.2 — Frontend UI

Keep this simple and inside the same FastAPI app rather than standing up a separate React/Vue project — it's faster to build, has no separate build pipeline to deploy, and keeps you to one Render service instead of two.

- Use **Jinja2 templates** (FastAPI has first-class support via `fastapi.templating.Jinja2Templates`) to server-render a handful of pages: a home/search page, a car-detail + prediction page (wraps the old sidebar form), and a price-movements page (Phase 2's CDC output).
- For interactivity (submitting the prediction form without a full page reload, refreshing the price-movements table) use **HTMX** — a single small script tag, no build step, and it pairs naturally with server-rendered HTML fragments returned from your existing FastAPI routes. This is a good, modern, low-complexity choice to name in your write-up. Plain vanilla JS with `fetch()` calls against your `/predict` and `/listings` JSON endpoints is a perfectly reasonable simpler fallback if you'd rather not add HTMX.
- Serve static assets (a small CSS file, maybe a logo) via FastAPI's `StaticFiles` mount — no need for a CDN or separate static host given there's no separate marketing site.
- Structure: `templates/` for Jinja2 HTML, `static/` for CSS/JS, `routers/pages.py` for the HTML-returning routes kept separate from `routers/api.py` for the JSON endpoints. This separation is a small thing that reads well: it shows you know the difference between a page route and an API route even when they live in the same app.

---

## Phase 5 — Data science improvements

1. **Feature engineering:** car age (`current_year - year`) instead of raw year, mileage-per-year, engine_cc as an ordered/binned feature, and — once CDC history exists — a rolling "days on market" or "price trend" feature per make/model.
2. **Model comparison, not just RandomForest:** add a simple baseline (linear regression or median-by-segment) and one gradient-boosted alternative (`XGBoost` or `LightGBM`) so your README can show a comparison table with MAE/R² per model, not a single unexamined number.
3. **Experiment tracking:** add **MLflow** (local file-store backend is fine — no server needed) to log params, metrics, and the trained model artifact per run. This is a high-signal, low-effort addition: a `mlruns/` folder and a couple of screenshots in the README go a long way for a DS-facing reviewer.
4. **Drift monitoring tied to CDC:** since Phase 2 gives you a real price-history stream, use it — periodically compare the distribution of newly scraped prices (for a given make/model/year bucket) against the distribution the model was trained on (e.g. a simple population stability index or even a rolling mean/std comparison is enough; don't reach for `evidently` unless you want the extra dependency). When drift crosses a threshold, log a `retrain_recommended` flag, surfaced on `GET /health`. This is the single best "DE feeds DS" story in the whole project — it's worth building even in a simplified form, because it's the concrete answer to "how do data engineering and data science actually connect here?"
5. **Model versioning:** save models as `models/car_price_model_{timestamp}.pkl` (or let MLflow manage this) instead of overwriting `car_price_model.pkl` on every train — you want to be able to roll back and to show a model history, and `/health` should report which version is currently being served.

---

## Phase 6 — Deployment plan

One FastAPI app (API + Jinja2 frontend together), one scheduled job, one database. No Streamlit, no separate static/marketing site.

| Component | Recommendation | Why / free-tier caveat |
|---|---|---|
| **FastAPI app (API + frontend)** | **Render** (free Web Service, run via `uvicorn`) | Render's free web services run a persistent Python process, which a real FastAPI app (not just a stateless function) needs — especially since it's also serving Jinja2 pages and holding a DB connection pool. Caveat: free services spin down after inactivity and cold-start on the next request (~30–60s) — name this explicitly in your README as a known limitation; it's normal for free-tier hosting and reviewers won't penalize you for naming it. Running everything as one service also means there's only one cold start to worry about, not two. |
| **Scheduled scraper / CDC job** | **GitHub Actions `schedule:` cron**, not Render | This is the right tool for a scheduled Python script: free, generous minutes on public repos, no cold-start concerns since it's not a long-running service. Have it call the same `scripts/run_pipeline.py` from Phase 3, writing directly to Neon. |
| **Database** | **Neon** (Postgres free tier) | One database, no alternatives to evaluate. Neon is a genuinely persistent, free, serverless Postgres instance that both the Render app and the GitHub Actions job connect to via the same connection string (stored as a secret in both places), which is what lets those two separate ephemeral environments share state correctly. Confirm current free-tier storage/compute limits before you commit, since they can change. |

Action items:

1. Add a `render.yaml` for the FastAPI service (start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
2. Add `.github/workflows/scrape.yml` with a `schedule:` cron (e.g. every 6 hours) running `scripts/run_pipeline.py`, with the Neon connection string as a repo secret.
3. Set the Neon connection string as an environment variable on Render too, so the app and the cron job are provably reading/writing the same data.
4. Keep the `GET /health` endpoint (last successful scrape timestamp, model version) visible on the frontend home page, so a recruiter opening the live link can immediately see the pipeline is actually running on a schedule, not just once at deploy time.

---

## Phase 7 — Portfolio polish

1. **Repo structure** — aim for:
   ```
   car-imports-advisor/
   ├── app/
   │   ├── main.py              # FastAPI app, mounts routers + static
   │   ├── routers/
   │   │   ├── api.py           # JSON endpoints
   │   │   └── pages.py         # Jinja2 HTML routes
   │   ├── templates/
   │   └── static/
   ├── scripts/
   │   └── run_pipeline.py
   ├── src/
   │   ├── scrapers/
   │   │   ├── base.py
   │   │   └── jiji.py
   │   ├── cdc.py
   │   ├── db_utils.py
   │   ├── calculator.py
   │   ├── ml_model.py
   │   └── schemas.py           # pydantic models, shared by scraper + API
   ├── migrations/
   ├── tests/
   ├── .github/workflows/
   │   ├── scrape.yml
   │   └── ci.yml
   ├── render.yaml
   └── README.md
   ```
2. **README architecture diagram** — a simple Mermaid diagram (renders natively on GitHub) showing: Jiji → Scraper → Validation → Neon (history + latest) → CDC diff → FastAPI (API + Jinja2/HTMX frontend) → deployed on Render, scheduled via GitHub Actions. This one diagram does more to communicate "I can design a system" than any amount of prose.
3. **CI** — a `.github/workflows/ci.yml` running `pytest` and a linter (`ruff` is the fast, low-config choice) on every push/PR. A green CI badge at the top of the README is a small, high-value signal.
4. **Project write-up** — add a `WRITEUP.md` or a README section structured as: *Problem → Data Engineering approach (scraping, validation, CDC, orchestration) → Data Science approach (features, model comparison, drift monitoring) → Backend/frontend and deployment architecture → What I'd do next at scale*. Explicitly label which parts are DE and which are DS — for a fullstack DS&DE audience, making that split legible is more valuable than burying it in code they'd have to read to find.
5. **Known limitations section** — name the free-tier cold start, the single-source scraping scope, and the simplified drift-detection method, explicitly. Reviewers in this field trust projects more, not less, when the author names the corners they cut and why.

---

## Prioritized roadmap summary

| Priority | Phase | Outcome if you stop here |
|---|---|---|
| Must-do (MVP) | 0, 1, part of 3 | Honest, working scraper feeding real SQLite/Neon data — no more fabricated results. This alone fixes the biggest credibility risk. |
| Should-do | 2, rest of 3, 4 | CDC price-history pipeline plus a working FastAPI backend and browsable frontend UI — this is where the system stops being "a script with a button" and starts being "an app." |
| DS depth | 5 | A defensible, tracked, drift-aware ML model — this is where the DE-meets-DS story becomes concrete. |
| Deploy | 6 | A live, scheduled, recruiter-clickable demo instead of a "clone and run locally" README. |
| Polish | 7 | The difference between "a working project" and "a project that reads as senior-level work" in a 5-minute recruiter skim. |

Ship in that order. Each phase is independently demo-able, so even a partially finished roadmap (e.g. Must-do + Should-do, no deployment yet) is still a legitimate, presentable portfolio state — don't let the deployment or polish phases block you from committing and sharing progress on the DE/DS core.
