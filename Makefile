GO ?= go
UV ?= server/.venv/bin/uv

.PHONY: dev dev-server dev-frontend dev-agent dev-reset backup restore build-agents build-frontend docker-up docker-down dev-deps dev-stop migrate test-server test-client lint-server fmt-client clean

dev:
	bash scripts/dev-start.sh

dev-server:
	cd server && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-agent:
	bash scripts/dev-agent.sh

dev-reset:
	docker compose -f docker-compose.dev.yml down -v && rm -f .env nginx/ssl/*.pem /tmp/dtsys-test.toml server/.env && echo "Reset complete — run make dev to start fresh"

backup:
	bash scripts/backup.sh

restore:
	bash scripts/restore.sh $(FILE)

build-agents:
	cd client && GOOS=linux GOARCH=amd64 $(GO) build -ldflags="-s -w -X main.AgentVersion=0.1.0" -o ../dist/agents/dtsys-agent-linux-amd64 ./cmd/agent/
	cd client && GOOS=linux GOARCH=arm64 $(GO) build -ldflags="-s -w -X main.AgentVersion=0.1.0" -o ../dist/agents/dtsys-agent-linux-arm64 ./cmd/agent/
	cd client && GOOS=windows GOARCH=amd64 $(GO) build -ldflags="-s -w -X main.AgentVersion=0.1.0" -o ../dist/agents/dtsys-agent-windows.exe ./cmd/agent/
	cd client && GOOS=darwin GOARCH=amd64 $(GO) build -ldflags="-s -w -X main.AgentVersion=0.1.0" -o ../dist/agents/dtsys-agent-darwin-amd64 ./cmd/agent/
	cd client && GOOS=darwin GOARCH=arm64 $(GO) build -ldflags="-s -w -X main.AgentVersion=0.1.0" -o ../dist/agents/dtsys-agent-darwin-arm64 ./cmd/agent/

build-frontend:
	cd frontend && npm run build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

dev-deps:
	docker compose -f docker-compose.dev.yml up -d

dev-stop:
	docker compose -f docker-compose.dev.yml down

migrate:
	cd server && .venv/bin/alembic upgrade head

test-server:
	cd server && .venv/bin/pytest tests/ -v

test-client:
	cd client && $(GO) test ./...

lint-server:
	cd server && .venv/bin/ruff check app/

fmt-client:
	gofmt -w $$(find client -name "*.go")

clean:
	rm -rf dist/agents/* frontend/dist client/bin
