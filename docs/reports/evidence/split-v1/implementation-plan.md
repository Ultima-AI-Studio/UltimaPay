# UltimaPay Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the complete payment backend out of UltimaGold into a standalone UltimaPay service that runs on port 8061 with its own SQLite database and `/api/pay/*` API.

**Architecture:** UltimaPay owns a FastAPI process, a versioned SQLite schema, the complete payment package, and the payment polling task. UltimaGold stops registering or starting payment logic and its consumers target the standalone service. The frozen work order is the binding specification; this plan only makes its test and file sequence explicit.

**Tech Stack:** Python 3.13, FastAPI, Uvicorn, SQLite, pytest, HTTPX, qrcode/Pillow.

**Spec:** `tasks/queue/wo-ultimapay-20261029-0001--split-from-gold.md`

## Global Constraints

- Execute T0 through Tn in order; do not enter the next task until the current Gate has fresh PASS evidence.
- Preserve amount locking, collision-avoidance tails, channel definitions, receipt state transitions, and callback behavior.
- New code and display content must not contain the retired brand token; only one explicitly documented compatibility-alias module may contain it.
- Do not change UltimaGold non-payment behavior.
- Use `ULTIMAPAY_DATABASE_URL` for the independent database; default to `backend/ultimapay.db`.
- Use `ULTIMAPAY_HOST` and `ULTIMAPAY_PORT`; default to `127.0.0.1:8061`.
- User-scoped order creation authenticates `X-Pay-User` with `X-Pay-Signature=HMAC-SHA256(ULTIMAPAY_USER_AUTH_SECRET, user)`; it never trusts a body user. Administrative routes require `Authorization: Bearer $ULTIMAPAY_ADMIN_TOKEN`. Merchant routes continue to use the persisted merchant key contract.
- Write every Gate result to `docs/reports/stage-split-v1.md` and supporting output to `docs/reports/evidence/split-v1/`.
- Commit and tag every Gate in the UltimaPay repository; final tag must be `wo-ultimapay-20261029-0001-frozen`.
- Do not commit virtual environments, databases, caches, logs, or dispatch files.

## Scope Rulings

- The work order names three payment files but its objective says the complete `payments` package and zero functional loss. Migrate all direct package files and chain adapters; otherwise the named files cannot import and UGP-16 through UGP-30 behavior disappears.
- The old product slug remains only in `app/compat.py` as a documented data/API compatibility value. All new modules consume the constant and do not repeat the retired token.
- UltimaPay and UltimaGold are not Git repositories at baseline. Initialize only UltimaPay as the independent product repository. Record before/after hashes for UltimaGold edits because no Gold repository exists to tag.
- The payment loop keeps payment-domain expiry, chain polling, and callback work. Gold membership fulfillment and invite rewards are non-payment consumers: rename the old mixed loop to a Gold entitlement loop containing only those two calls, keep its thread in Gold, and do not move their rules into UltimaPay.
- Gold `membership/fulfillment.py` currently imports `payments.outbox`. Move the exact `EVENT_VERIFIED` and `enqueue_event` helper into `membership/payment_events.py` and change only that import; the helper keeps the same injected connection and same-transaction write semantics. Then remove the entire Gold `payments` package. Existing historical table names remain unchanged, as required by the wider brand migration decision.
- The live-source prohibited-path Gate excludes immutable historical evidence (`docs/reports`, `docs/work-orders`, `_backup`) and the current work order/evidence. Save the exact exclusion list and report historical matches separately; live application, SDK, scripts, tests, and display documentation must have zero old payment-path matches outside an explicitly marked compatibility adapter.
- Mechanical replacement of old product-slug literals with the single compatibility constant is allowed in the migrated package. Behavior tests, not textual equality, prove that pricing, allocation, state transitions, and channel values are unchanged.
- UltimaPay tags freeze only the UltimaPay repository. Before changing unversioned Gold files, capture hashes and temporary originals outside both products; after editing, save forward/reverse patches plus hashes in evidence and remove the temporary copies.

## Review Focus

- Cold start with no database creates every table required by routes and the complete payment package, including the exact m009 session columns.
- Missing or invalid caller identity cannot silently fall back to an official merchant.
- Repeated order creation with one idempotency key returns one order and preserves amount-slot uniqueness.
- Receipt/callback input never promotes an order directly to confirmed without verified chain evidence.
- The loop survives disabled live polling or unreachable chain providers without stopping the API.
- User/admin/merchant identity failures return 401/403 and never fall back to a platform identity.

---

### Task 0: Standalone service skeleton and repository baseline

**Files:**
- Create: `.gitignore`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/run.py`
- Create: `backend/requirements.txt`
- Create: `backend/app/config.py`
- Create: `backend/tests/test_t0_service.py`
- Create/update: `docs/reports/stage-split-v1.md`

**Interfaces:**
- Produces: `app.main.app: FastAPI`, `GET /health`, `run.main()`, settings for port/database/auth, default port 8061.

- [ ] Write `test_t0_service.py` asserting the app title and real `/health` response.
- [ ] Run it before the skeleton exists; expect failure caused by the missing `app.main` module.
- [ ] Implement the minimal app and runner, with no payment imports.
- [ ] Create ignored `backend/.venv`, install `backend/requirements.txt`, and record exact package versions; expect FastAPI/Uvicorn/HTTPX/pytest/qrcode/Pillow imports to pass.
- [ ] From `backend/`, run `.venv/Scripts/python.exe -m pytest tests/test_t0_service.py -q`; expect PASS with `app` resolved from the fixed working directory.
- [ ] From `backend/`, start `.venv/Scripts/python.exe run.py`, request `http://127.0.0.1:8061/health`, stop only that process, and save status/body evidence.
- [ ] Initialize the UltimaPay Git repository, commit T0, and create tag `wo-ultimapay-20261029-0001-t0-pass`.

### Task 1: Complete payment package migration

**Files:**
- Create: `backend/app/contracts.py`
- Create: `backend/app/catalog.py`
- Create: `backend/app/compat.py`
- Create: `backend/app/auth/policy.py`
- Create: `backend/app/storage.py`
- Create: `backend/app/payments/**`
- Create: `backend/tests/test_t1_payments.py`
- Update: `docs/reports/stage-split-v1.md`

**Interfaces:**
- Consumes: DB-API SQLite connection and `catalog.read_subscription_plans(db)`.
- Produces: the source payment public functions, `_PAYMENT_CHANNELS`, merchant sessions, order allocation, verification, settlement, callbacks, reconciliation, and chain parsers under `app.payments`.
- Produces: `storage.open_connection()` plus `storage.ensure_payment_base_schema()` containing the unchanged payment base tables/seeds required for a real T2 cold start. Task 3 moves the same DDL under formal versioned migration ownership and adds m009; it does not change this provisional schema's behavior.

- [ ] Write tests for importability, exact channel payloads, amount locking/tail allocation, tamper rejection, idempotency, and receipt staying submitted.
- [ ] Run the tests; expect failure because `app.payments` does not exist.
- [ ] Copy the complete payment package and chain adapters; change only package imports, logging namespace, and compatibility/catalog access required for independence.
- [ ] Replace every migrated old product-slug literal mechanically with `compat.LEGACY_PRODUCT_REF`; permit the literal only in that documented compatibility module.
- [ ] Implement the payment-only base schema and unchanged seeds in `storage.ensure_payment_base_schema()`, and test a brand-new default database can import the package and execute its base queries.
- [ ] From `backend/`, run `.venv/Scripts/python.exe -m pytest tests/test_t1_payments.py -q`; expect PASS.
- [ ] Run a normalized function/signature comparison against the Gold source and save the diff summary.
- [ ] Commit T1 and create tag `wo-ultimapay-20261029-0001-t1-pass`.

### Task 2: FastAPI routes and payment loop

**Files:**
- Create: `backend/app/routes/__init__.py`
- Create: `backend/app/routes/payment.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/payment_loop.py`
- Update: `backend/app/main.py`
- Create: `backend/tests/test_t2_routes.py`
- Modify: `H:/UltimaAI/UltimaGold/news-feed/feed_server.py`
- Update: `docs/reports/stage-split-v1.md`

**Interfaces:**
- Consumes: `storage.open_connection()`, `storage.ensure_payment_base_schema()`, and `app.payments` functions. The T2 lifespan invokes the base-schema bootstrap, so default production cold start and route calls are real; Task 3 subsequently formalizes version tracking and m009.
- Produces: the routes that exist in `feed_server.py`: `/api/pay/qrcode`, `/order`, `/channels`, `/merchant-orders`, `/tickets`, `/merchants`, `/address`, `/orders`, `/apply`, `/verify`, `/tickets/process`, `/ticket`, `/config`, `/create`, `/callback`, `/confirm`, `/expire`; plus `run_payment_iteration()` and an app-lifespan task. Merchant-intent/session routes wait for m009 in Task 3.

- [ ] Write route tests that assert the OpenAPI paths, HMAC user authentication, admin bearer authentication, merchant rejection without fallback, query/order/channel/qrcode behavior against a brand-new default database, callback rejection without a secret, receipt submission semantics, and a loop iteration heartbeat.
- [ ] Run the tests; expect failure because the router and loop do not exist.
- [ ] Implement the routes by preserving the old handler branch semantics and response envelope under `/api/pay/*`.
- [ ] Register the payment task in FastAPI lifespan with the original 30-second default interval and live-chain switch.
- [ ] Capture Gold originals/hashes in a disposable external temp directory. Remove the payment constants, wrappers, payment-domain loop operations, route branches, alias normalizer, and payment thread start from `feed_server.py`; preserve `process_fulfillments` and `process_invite_rewards` in a renamed entitlement loop/thread so Gold non-payment behavior remains available.
- [ ] From `backend/`, run `.venv/Scripts/python.exe -m pytest tests/test_t2_routes.py -q`; expect PASS, then use the same interpreter to import/compile the edited Gold server.
- [ ] Start the real service without dependency overrides, call the registered routes against its cold-started database, and save the process/HTTP/heartbeat evidence.
- [ ] Save OpenAPI path and loop-heartbeat evidence; commit T2 in UltimaPay and tag `wo-ultimapay-20261029-0001-t2-pass`.

### Task 3: Independent database and m009 migration

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/migrations/__init__.py`
- Create: `backend/migrations/0009_payment_pay_sessions.py`
- Create: `backend/tests/test_t3_database.py`
- Modify: `backend/app/storage.py`
- Modify: `backend/app/main.py`
- Modify if needed: `backend/app/routes/payment.py`
- Modify: `H:/UltimaAI/UltimaGold/backend/ultima_backend/db.py`
- Update: `docs/reports/stage-split-v1.md`

**Interfaces:**
- Produces: `database_path()`, `connection()`, `migrate()`, standalone base payment schema, version ledger, and exact m009 table/index. `storage.open_connection()` delegates to `db.connection()` and startup calls `db.migrate()` before the router or loop runs. After m009 exists, the router adds merchant-intent creation and pay-session order retrieval endpoints backed by `app.payments.merchant_api`.
- Consumed by: Task 2 route dependencies and startup lifespan.

- [ ] Write cold-start tests with a temporary SQLite URL, exact m009 column assertions, payment table assertions, and a guard that the path is outside UltimaGold.
- [ ] Run the tests; expect failure because `app.db` and the migration do not exist.
- [ ] Implement payment-only base schema and migrations; seed unchanged channels, merchant records, plans, and the smoke-test user required for standalone order flow.
- [ ] Wire migrate-on-start and rerun cold-start tests; expect PASS.
- [ ] Add and test merchant-intent/session endpoints only after m009 migration; a cold database must create an intent, persist its session, and retrieve only the bound order.
- [ ] Start the app with a new database and no dependency override, then call health/channels/authenticated order/query to prove the production factory uses the migrated database.
- [ ] Remove `_m009_payment_pay_sessions`, its migration entry, and the corresponding Gold schema version ownership without changing other Gold migrations.
- [ ] Delete a disposable DB, cold-start the service, inspect the table, and save evidence.
- [ ] Commit T3 and create tag `wo-ultimapay-20261029-0001-t3-pass`.

### Task 4: Detach Gold and update callers

**Files:**
- Remove: `H:/UltimaAI/UltimaGold/backend/ultima_backend/payments/**`
- Create: `H:/UltimaAI/UltimaGold/backend/ultima_backend/membership/payment_events.py`
- Modify: `H:/UltimaAI/UltimaGold/backend/ultima_backend/membership/fulfillment.py` (one import rewire only)
- Modify payment callers under `H:/UltimaAI/UltimaGold/` only where they call the retired payment service.
- Create: `backend/tests/test_t4_gold_detached.py`
- Update: `docs/reports/stage-split-v1.md`

**Interfaces:**
- Produces: Gold with no payment process, payment API registration, session-table ownership, or payment business package. The entitlement helper retains its historical table name and transactional behavior under membership ownership. Callers use `ULTIMAPAY_BASE_URL` defaulting to `http://127.0.0.1:8061`.

- [ ] Write executable/static boundary tests for no payment thread/route/session schema/package logic, unchanged same-transaction entitlement event creation, and new caller URLs.
- [ ] Run the tests; expect failure on the remaining Gold payment ownership.
- [ ] Remove the remaining payment package logic and update only payment callers/tests/scripts that are live integration points.
- [ ] Generate forward and reverse Gold patches plus before/after SHA-256 evidence from the temporary originals, then delete the temporary copies.
- [ ] From `backend/`, run the boundary tests with `.venv/Scripts/python.exe`, import `membership.fulfillment`, compile Gold Python, and run the selected Gold non-payment regression set; record every failure by name.
- [ ] With Gold stopped, start only UltimaPay and exercise health plus core payment endpoints.
- [ ] Commit T4 evidence and create tag `wo-ultimapay-20261029-0001-t4-pass`.

### Task 5: SDK, project index, and migration documentation

**Files:**
- Create: `docs/INDEX.md`
- Create/update: `README.md`
- Copy/update: `sdk/**`
- Modify: `H:/UltimaAI/UltimaGold/ultima-pay/README.md`
- Modify only payment-related documents under `H:/UltimaAI/UltimaGold/ultima-pay/docs/`
- Create: `backend/tests/test_t5_sdk_contract.py`
- Update: `docs/reports/stage-split-v1.md`

**Interfaces:**
- Produces: SDK defaults to `/api/pay/*` and `http://127.0.0.1:8061`; project index maps authoritative sources, startup, port, directories, and prohibited naming.

- [ ] Write SDK contract tests for base URL/path behavior and migrate/copy behavior.
- [ ] Run them; expect failure before the SDK is present or while old paths remain.
- [ ] Copy the SDK, update only service addressing, and add project/Gold migration documentation.
- [ ] Run SDK tests/build if its package manifest supports it; run Python contract tests from `backend/` with `.venv/Scripts/python.exe`; expect PASS.
- [ ] Scan UltimaPay plus Gold live payment display/docs; allow the retired token only in the explicit compatibility module and immutable historical work-order/evidence inputs. Save the exact exclusion list and a separate count of historical matches.
- [ ] Commit T5 and create tag `wo-ultimapay-20261029-0001-t5-pass`.

### Task n: Total acceptance, independent audit, and freeze

**Files:**
- Finalize: `docs/reports/stage-split-v1.md`
- Finalize evidence under: `docs/reports/evidence/split-v1/`
- Update: `tasks/queue/ultimapay-queue-index.md`

**Interfaces:**
- Produces: a reproducible acceptance record and the exact frozen tag.

- [ ] From `backend/`, run `.venv/Scripts/python.exe -m pytest -q` and `.venv/Scripts/python.exe -m compileall app migrations run.py`; expect zero failures/errors.
- [ ] Start only UltimaPay with test-only HMAC/admin secrets and perform real health, authenticated create-order, query-order, qrcode, and receipt calls; save requests, status codes, and redacted bodies.
- [ ] Re-run amount-locking, collision-slot, idempotency, and no-direct-confirm boundary tests.
- [ ] Run the prohibited-token scan and classify only the explicit compatibility/historical evidence exceptions.
- [ ] Run the Gold boundary and selected non-payment regression checks with Gold services stopped.
- [ ] Obtain a fresh independent code review; fix Critical/Important findings in one RED-to-GREEN pass and ledger any rulings/minors.
- [ ] Finalize the report and eight-field receipt, commit, create `wo-ultimapay-20261029-0001-frozen`, and verify `git rev-parse` resolves it.
