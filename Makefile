# Graphknows — developer command centre
# ──────────────────────────────────────────────────────────────────
# First run:  make setup   (creates .env)
# Start:      make up
# Shell:      make shell SVC=workspace
# ──────────────────────────────────────────────────────────────────
COMPOSE := docker compose
CONTAINER ?= processrecall-workspace
SVC     ?= workspace   # service `make shell`/`logs`/`restart`/`stop`/`build` targets

.PHONY: up down restart stop build rebuild ps logs shell infra-up infra-down setup clean prune \
        test test-integration lint typecheck arch build-wheel smoke bake eval-smoke sweep ci gate help \
        sonar sonar-gate sonar-gate-init sonar-up sonar-down sonar-token

# ── Lifecycle ──────────────────────────────────────────────────────
up: ## Start all services
	$(COMPOSE) up -d --remove-orphans
	@echo ""
	@$(MAKE) --no-print-directory ps

down: ## Stop and remove containers (data volumes preserved)
	$(COMPOSE) down --remove-orphans

restart: ## Restart one or all services  →  make restart SVC=workspace
	$(COMPOSE) restart $(SVC)

stop: ## Stop (but don't remove) one or all services  →  make stop SVC=workspace
	$(COMPOSE) stop $(SVC)

ps: ## Show running containers and their status
	$(COMPOSE) ps

logs: ## Tail logs — all services or one  →  make logs SVC=workspace
	$(COMPOSE) logs -f --tail=100 $(SVC)

shell: ## Open a shell in a running container  →  make shell SVC=workspace
	$(COMPOSE) exec $(SVC) /bin/bash

# ── Build ──────────────────────────────────────────────────────────
build: ## Build images (cached)  →  make build SVC=workspace
	$(COMPOSE) build $(SVC)

rebuild: ## Force-rebuild images (no cache)  →  make rebuild SVC=workspace
	$(COMPOSE) build --no-cache $(SVC)

# ── Infra-only ────────────────────────────────────────────────────
infra-up: ## Start only the database (ArcadeDB)
	$(COMPOSE) up -d arcadedb

infra-down: ## Stop the database
	$(COMPOSE) stop arcadedb

# ── Setup / cleanup ───────────────────────────────────────────────
clean: ## Stop containers and remove images built from this project
	$(COMPOSE) down --rmi local

prune: ## ⚠ Remove all stopped containers, unused images and networks
	docker system prune -f

setup: ## First-time setup: copy .env.example → .env if missing
	@if [ ! -f .env ]; then \
	  cp .env.example .env; \
	  echo "  Created .env from .env.example — fill in the REQUIRED keys before 'make up'"; \
	else \
	  echo "  .env already exists"; \
	fi

# ── Quality gates (local, no Docker) ──────────────────────────────
test: ## Run the unit test suite (integration tests excluded)
	pytest -q -m "not integration"

test-integration: ## Run integration tests (needs a live ArcadeDB — make infra-up)
	pytest -q -m integration

lint: ## Ruff lint the package + tests
	ruff check processrecall tests

typecheck: ## Mypy the package
	mypy processrecall

arch: ## Enforce module boundaries (import-linter)
	lint-imports

build-wheel: ## Build the wheel and assert it ships py.typed (size ceiling: tests/test_packaging.py)
	uv build --wheel
	@python -c "import glob,os,zipfile; w=sorted(glob.glob('dist/*.whl'))[-1]; \
	  z=zipfile.ZipFile(w); assert any('py.typed' in n for n in z.namelist()), 'py.typed missing'; \
	  print(f'wheel OK: {os.path.basename(w)} ({os.path.getsize(w)/1024:.0f} KB, py.typed present)')"

smoke: ## Core-only import smoke: import processrecall + client with no heavy deps
	pytest -q tests/api/test_lazy_core.py tests/api/test_public_surface.py

ci: gate build-wheel ## Run the full local CI gate (scripts/gate.sh + the wheel checks)

gate: ## Run the pre-push gate exactly as the hook runs it
	bash scripts/gate.sh

# Report-only on purpose. vulture false-positives on pydantic fields, protocol
# methods and lazy __getattr__, so it is a sweep a human reads, never a gate.
# Its job is the one thing no gate catches: code that is wired to nothing.
sweep: ## Dead-code sweep (report only, run every few weeks)
	docker exec $(CONTAINER) sh -c "cd /app && uvx vulture processrecall \
	  --min-confidence 60"

# ── Model prep ─────────────────────────────────────────────────────
# Run once after `make up` on a fresh model_cache volume, or after adding/
# changing a model in processrecall/settings.py — populates model_cache so the
# first real ingest doesn't pay the multi-GB download inline.
bake: ## Pre-warm the GLiNER/sentence-transformers/spaCy/NLTK model cache
	docker exec $(CONTAINER) sh -c "cd /app && python scripts/bake_models.py"

eval-smoke: ## End-to-end acceptance: one LoCoMo conversation via the MCP client (needs services up)
	python -m evaluation --limit 1

# ── Static analysis (local SonarQube, opt-in) ─────────────────────
# Advisory, not a gate: `make sonar` reports and does not fail the build. It is
# deliberately NOT wired into scripts/gate.sh or ci.yml — those thresholds are
# unmeasured, and a gate nobody has calibrated only teaches people to route
# around it. Promote it the way diff coverage is being promoted: after a few
# runs of real numbers.
#
# Nothing leaves this machine: the scanner reports to the container `sonar-up`
# starts, never to SonarSource.
#
# First run, in order:
#   make sonar-up      # cold start is minutes (embedded H2 + Elasticsearch)
#   make sonar-token   # says where to mint the token
#   make test          # writes coverage.xml, which the scan reads
#   make sonar
SONAR_PORT ?= 9000
SONAR_HOST_URL ?= http://localhost:$(SONAR_PORT)
SONAR_PROJECT_KEY ?= processrecall
# make does not read .env. Export it in your shell, or pass it inline:
#   make sonar SONAR_TOKEN=sqa_...
SONAR_TOKEN ?=
SONAR_SCANNER_IMAGE ?= sonarsource/sonar-scanner-cli:12.1.0.3233_8.0.1
# A NAMED volume, not ./.sonar. The scanner provisions its own JRE on first run
# and extracts it with an atomic rename, which raises AccessDeniedException on a
# Windows bind mount — the scan dies before analysis starts. A named volume is
# also the layout the vendor's own docs mount. (`.sonar` stays in .gitignore
# regardless: it is the fallback cache path if anyone runs the scanner on the
# host, and an accidental commit of an analyzer cache is what that entry guards.)
SONAR_CACHE_VOLUME ?= processrecall_sonar_cache

sonar-up: ## Start the local SonarQube server (opt-in profile — `make up` does not)
	$(COMPOSE) --profile sonar up -d sonarqube
	@echo "  waiting for SonarQube to report UP (first boot takes minutes)…"
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' processrecall-sonarqube 2>/dev/null)" = "healthy" ]; do \
	  if [ -z "$$(docker ps -q -f name=processrecall-sonarqube)" ]; then \
	    echo "  sonarqube is not running — see 'make logs SVC=sonarqube'"; exit 1; \
	  fi; \
	  sleep 5; \
	done
	@echo "  SonarQube is up: $(SONAR_HOST_URL)"

sonar-down: ## Stop the local SonarQube server (history survives in named volumes)
	$(COMPOSE) --profile sonar stop sonarqube

sonar-token: ## How to mint the two tokens the sonar targets need
	@echo "  $(SONAR_HOST_URL) -> My Account -> Security -> Generate Token."
	@echo "    'Global Analysis Token' (sqa_) for: make sonar, make sonar-gate"
	@echo "    'User Token' (squ_) for: make sonar-gate-init -- SonarQube bars analysis"
	@echo "    tokens from administering gates, whoever owns them, so sqa_ fails there by design."

sonar: ## Scan this tree with the local SonarQube (advisory — never fails the build)
	@if [ -z "$(SONAR_TOKEN)" ]; then \
	  echo "  SONAR_TOKEN is unset — run 'make sonar-token'."; exit 1; \
	fi
	@test -f coverage.xml || \
	  echo "  no coverage.xml — run 'make test' first, or the report lands at 0% coverage"
	@docker volume create $(SONAR_CACHE_VOLUME) >/dev/null
	-docker run --rm --network host \
	  -e SONAR_HOST_URL="$(SONAR_HOST_URL)" \
	  -e SONAR_TOKEN="$(SONAR_TOKEN)" \
	  -v "$(CURDIR):/usr/src" \
	  -v "$(SONAR_CACHE_VOLUME):/opt/sonar-scanner/.sonar" \
	  $(SONAR_SCANNER_IMAGE) \
	  -Dsonar.projectKey=$(SONAR_PROJECT_KEY) \
	  -Dsonar.projectName=$(SONAR_PROJECT_KEY) \
	  -Dsonar.sources=processrecall \
	  -Dsonar.tests=tests \
	  -Dsonar.sourceEncoding=UTF-8 \
	  -Dsonar.python.version=3.11,3.12,3.13 \
	  -Dsonar.python.coverage.reportPaths=coverage.xml \
	  -Dsonar.exclusions=research/**,evaluation/**,docs/** \
	  -Dsonar.scm.provider=git
	@echo "  report: $(SONAR_HOST_URL)/dashboard?id=$(SONAR_PROJECT_KEY)"

# The gate as code, because the server it configures is a container: click the gate together
# by hand and it dies with `docker rm`, and `make sonar-up` starts a DIFFERENT one whose named
# volumes have never heard of it. Idempotent, so re-running it against a fresh server is the
# whole recovery procedure. Needs a USER token (squ_), not the analysis token `make sonar` uses.
#
# Every call is checked. The first version of this ended each curl with `|| true` and sent the
# output to /dev/null, so a 403 printed "gate applied" having done nothing -- a config step that
# claims a success it did not have is worse than one that fails, because nothing downstream can tell.
#
# The conditions are Clean-as-You-Code and scoped to NEW code: this tree carries pre-existing
# violations, and gating on overall code would block every task for debt no task introduced.
# The new-code period is set here too, and matters as much: a project left on PREVIOUS_VERSION
# that never sets sonar.projectVersion has an EMPTY new-code window, so every condition above
# passes on nothing at all -- a gate that reports OK and proves exactly nothing.
SONAR_GATE_NAME ?= processrecall-gate

define SONAR_GATE_INIT_PY
import base64, json, os, sys, urllib.parse, urllib.request, urllib.error

BASE = os.environ['SONAR_HOST_URL'].rstrip('/')
TOKEN = os.environ['SONAR_TOKEN']
PROJECT = os.environ['SONAR_PROJECT_KEY']
GATE = os.environ.get('SONAR_GATE_NAME') or 'processrecall-gate'

if TOKEN.startswith('sqa_') or TOKEN.startswith('sqp_'):
    sys.exit('sonar: SONAR_TOKEN is an ANALYSIS token, which SonarQube bars from administering'
             + chr(10) + '  gates no matter who owns it. Mint a USER token (squ_) instead:'
             + chr(10) + '  ' + BASE + ' -> My Account -> Security -> Generate, type User Token.')

CONDITIONS = [
    ('new_violations', 'GT', '0'),
    ('new_coverage', 'LT', '80'),
    ('new_duplicated_lines_density', 'GT', '3'),
    ('new_security_hotspots_reviewed', 'LT', '100'),
    ('new_reliability_rating', 'GT', '1'),
    ('new_security_rating', 'GT', '1'),
    ('new_maintainability_rating', 'GT', '1'),
]

def post(path, **params):
    req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(params).encode(), method='POST')
    req.add_header('Authorization', 'Basic ' + base64.b64encode((TOKEN + ':').encode()).decode())
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        if 'already exists' in detail:
            return
        sys.exit('sonar: ' + path + ' -> HTTP ' + str(e.code) + chr(10) + '  ' + detail[:300])
    except Exception as e:
        sys.exit('sonar: cannot reach ' + BASE + path + ': ' + str(e))

post('/api/qualitygates/create', name=GATE)
for metric, op, error in CONDITIONS:
    post('/api/qualitygates/create_condition', gateName=GATE, metric=metric, op=op, error=error)
post('/api/qualitygates/select', gateName=GATE, projectKey=PROJECT)
post('/api/new_code_periods/set', project=PROJECT, branch='main', type='NUMBER_OF_DAYS', value='1')
print('  gate ' + GATE + ' applied to ' + PROJECT + ' with ' + str(len(CONDITIONS)) + ' conditions')
endef
export SONAR_GATE_INIT_PY

sonar-gate-init: ## Create the quality gate on the local server and bind it (needs a USER token)
	@test -n "$(SONAR_TOKEN)" || { echo "  SONAR_TOKEN is unset - run 'make sonar-token'."; exit 1; }
	@SONAR_HOST_URL='$(SONAR_HOST_URL)' SONAR_TOKEN='$(SONAR_TOKEN)' SONAR_PROJECT_KEY='$(SONAR_PROJECT_KEY)' SONAR_GATE_NAME='$(SONAR_GATE_NAME)' python -c "$$SONAR_GATE_INIT_PY"

# Reads back the gate the scan above was judged by. REPORT-ONLY by default,
# on purpose and for the same reason DIFF_COVERAGE_MIN starts at 0 in ci.yml:
# the conditions are Clean-as-You-Code defaults nobody here has yet watched
# run against a real task. Set SONAR_GATE_STRICT=1 once a few runs of measured
# numbers say the thresholds are the right ones, and it starts failing.
SONAR_GATE_STRICT ?= 0

sonar-gate: ## Report the quality gate for the last scan (report-only unless SONAR_GATE_STRICT=1)
	@test -n "$(SONAR_TOKEN)" || { echo "  SONAR_TOKEN is unset - run 'make sonar-token'."; exit 1; }
	@curl -sf -u "$(SONAR_TOKEN):" "$(SONAR_HOST_URL)/api/qualitygates/project_status?projectKey=$(SONAR_PROJECT_KEY)" | python -c "import sys,json;d=json.load(sys.stdin)['projectStatus'];print('  gate:',d['status']);[print('   ',c['metricKey'],c['status'],c.get('actualValue'),'vs',c.get('errorThreshold')) for c in d['conditions']];sys.exit(1 if d['status']=='ERROR' and '$(SONAR_GATE_STRICT)'=='1' else 0)"

# ── Help ──────────────────────────────────────────────────────────
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' | sort

.DEFAULT_GOAL := help
