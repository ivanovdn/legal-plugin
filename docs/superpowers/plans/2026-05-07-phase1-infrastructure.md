# Phase 1 — Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up project skeleton, Docker services, configuration, and Qdrant collections so all infrastructure is running and verified before any application code.

**Architecture:** FastAPI backend + Chainlit frontend (separate processes), with Qdrant, Redis, and Langfuse in Docker. Ollama and llama-cpp run natively on macOS. Configuration via pydantic-settings loading from `.env`.

**Tech Stack:** Python 3.12, pydantic-settings, Docker Compose, Qdrant, Redis, Langfuse, pytest

---

## File Structure

```
legal-plugin/
|-- .env.example          # Template with all env vars, no secrets
|-- .env                  # Local config (gitignored)
|-- .gitignore
|-- config.py             # pydantic-settings BaseSettings, single import
|-- docker-compose.yml    # Qdrant, Redis, Langfuse
|-- requirements.txt      # All Python dependencies
|-- scripts/
|   +-- create_collections.py   # Creates 3 Qdrant collections
+-- tests/
    +-- test_config.py          # Config loading tests
```

---

### Task 1: Initialize git repo and .gitignore

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: Initialize git repo**

Run:
```bash
cd /Users/dmytroivanov/projects/legal-plugin
git init
```

- [ ] **Step 2: Create .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/

# Environment
.env

# Data
data/

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store

# Chainlit
.chainlit/
chainlit.md
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: initialize repo with .gitignore"
```

---

### Task 2: Create .env.example and .env

**Files:**
- Create: `.env.example`
- Create: `.env` (gitignored, local only)

- [ ] **Step 1: Create .env.example**

This is the template. No secrets, no model names — those are filled in by the developer.

```bash
# LLM
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=
EMBEDDING_MODEL=
QDRANT_VECTOR_DIM=

# Qdrant
QDRANT_URL=http://localhost:6333

# Redis
REDIS_URL=redis://localhost:6379

# Reranker
RERANKER_ENABLED=true
RERANKER_BACKEND=
RERANKER_URL=
RERANKER_MODEL=
RERANKER_TOP_N=6
RERANKER_CANDIDATES=25

# BM25
BM25_ENABLED=false

# Langfuse
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# Phoenix
PHOENIX_HOST=http://localhost:6006

# App
API_PORT=8000
CHAINLIT_PORT=8080
SQLITE_PATH=data/legal.db
```

- [ ] **Step 2: Copy to .env for local use**

```bash
cp .env.example .env
```

Then fill in `LLM_MODEL`, `EMBEDDING_MODEL`, `QDRANT_VECTOR_DIM`, `RERANKER_BACKEND`, `RERANKER_URL`, `RERANKER_MODEL` with actual values for your local setup.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "chore: add .env.example with all config vars"
```

---

### Task 3: Create requirements.txt

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: Create requirements.txt**

Pin major versions. These are all dependencies needed across all phases — install once.

```txt
# API
fastapi>=0.115,<1.0
uvicorn[standard]>=0.34,<1.0

# LangGraph
langgraph>=0.4,<1.0
langchain-ollama>=0.3,<1.0
langchain-qdrant>=0.2,<1.0

# RAG
qdrant-client>=1.13,<2.0

# BM25
rank-bm25>=0.2,<1.0

# Document parsing
pdfplumber>=0.11,<1.0
python-docx>=1.1,<2.0

# Config
pydantic>=2.0,<3.0
pydantic-settings>=2.0,<3.0

# Session / checkpointer
redis>=5.0,<6.0
langgraph-checkpoint-redis>=0.1,<1.0

# Audit
aiosqlite>=0.20,<1.0

# Observability
langfuse>=2.0,<3.0

# Frontend
chainlit>=2.0,<3.0

# HTTP client (for reranker, Ollama direct calls)
httpx>=0.28,<1.0

# Testing
pytest>=8.0,<9.0
pytest-asyncio>=0.24,<1.0
```

- [ ] **Step 2: Create virtual environment and install**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 3: Verify key imports**

```bash
python -c "import fastapi; import langgraph; import qdrant_client; import pydantic_settings; print('All imports OK')"
```

Expected: `All imports OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add requirements.txt with all dependencies"
```

---

### Task 4: Create config.py

**Files:**
- Create: `config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/__init__.py` (empty) and `tests/test_config.py`:

```python
# tests/test_config.py
import os
import pytest


def test_config_loads_from_env(monkeypatch):
    """Config loads all fields from environment variables."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("LLM_MODEL", "llama3")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("RERANKER_TOP_N", "6")
    monkeypatch.setenv("RERANKER_CANDIDATES", "25")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("CHAINLIT_PORT", "8080")
    monkeypatch.setenv("SQLITE_PATH", "data/legal.db")

    from config import Settings
    settings = Settings()

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.llm_model == "llama3"
    assert settings.embedding_model == "nomic-embed-text"
    assert settings.qdrant_vector_dim == 768
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.redis_url == "redis://localhost:6379"
    assert settings.reranker_enabled is True
    assert settings.reranker_backend == "llama-cpp"
    assert settings.reranker_url == "http://localhost:8081/v1/rerank"
    assert settings.reranker_model == "bge-reranker"
    assert settings.reranker_top_n == 6
    assert settings.reranker_candidates == 25
    assert settings.bm25_enabled is False
    assert settings.langfuse_host == "http://localhost:3000"
    assert settings.langfuse_public_key == "pk-test"
    assert settings.langfuse_secret_key == "sk-test"
    assert settings.phoenix_host == "http://localhost:6006"
    assert settings.api_port == 8000
    assert settings.chainlit_port == 8080
    assert settings.sqlite_path == "data/legal.db"


def test_config_singleton_returns_same_instance(monkeypatch):
    """get_settings() returns the same cached instance."""
    monkeypatch.setenv("LLM_MODEL", "llama3")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write config.py**

```python
# config.py
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = ""
    embedding_model: str = ""
    qdrant_vector_dim: int = 0

    # Qdrant
    qdrant_url: str = "http://localhost:6333"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Reranker
    reranker_enabled: bool = True
    reranker_backend: str = ""
    reranker_url: str = ""
    reranker_model: str = ""
    reranker_top_n: int = 6
    reranker_candidates: int = 25

    # BM25
    bm25_enabled: bool = False

    # Langfuse
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Phoenix
    phoenix_host: str = "http://localhost:6006"

    # App
    api_port: int = 8000
    chainlit_port: int = 8080
    sqlite_path: str = "data/legal.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add config.py tests/__init__.py tests/test_config.py
git commit -m "feat: add config.py with pydantic-settings and tests"
```

---

### Task 5: Create docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
services:
  qdrant:
    image: qdrant/qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./data/qdrant:/qdrant/storage
    restart: unless-stopped

  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
    restart: unless-stopped

  langfuse:
    image: langfuse/langfuse:latest
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: "file:/data/langfuse.db"
      NEXTAUTH_SECRET: "local-dev-secret"
      NEXTAUTH_URL: "http://localhost:3000"
      SALT: "local-dev-salt"
    volumes:
      - ./data/langfuse:/data
    restart: unless-stopped
```

- [ ] **Step 2: Create data directory**

```bash
mkdir -p data
```

- [ ] **Step 3: Start services**

```bash
docker compose up -d
```

- [ ] **Step 4: Verify all services are running**

```bash
docker compose ps
```

Expected: All 3 services show `running` status.

- [ ] **Step 5: Verify Qdrant is reachable**

```bash
curl -s http://localhost:6333/healthz
```

Expected: `ok` or similar healthy response.

- [ ] **Step 6: Verify Redis is reachable**

```bash
redis-cli ping
```

Expected: `PONG`

- [ ] **Step 7: Verify Langfuse is reachable**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

Expected: `200` (or `302` redirect to login — both mean it's running).

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose with Qdrant, Redis, Langfuse"
```

---

### Task 6: Create Qdrant collections script

**Files:**
- Create: `scripts/__init__.py` (empty)
- Create: `scripts/create_collections.py`

- [ ] **Step 1: Create scripts/create_collections.py**

This script creates the 3 Qdrant collections defined in the spec. It reads `QDRANT_URL` and `QDRANT_VECTOR_DIM` from config. It is idempotent — re-running it skips existing collections.

```python
#!/usr/bin/env python3
# scripts/create_collections.py
"""Create Qdrant collections for the legal plugin. Idempotent — skips existing."""

import sys
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from config import get_settings

COLLECTIONS = [
    {
        "name": "legal_docs",
        "description": "Contracts, legislation, templates, policies",
    },
    {
        "name": "case_history",
        "description": "Past signed contracts — clause-level chunks",
    },
    {
        "name": "memory",
        "description": "Attorney preferences and past decisions",
    },
]


def create_collections() -> None:
    settings = get_settings()

    if settings.qdrant_vector_dim == 0:
        print("ERROR: QDRANT_VECTOR_DIM is not set in .env")
        sys.exit(1)

    client = QdrantClient(url=settings.qdrant_url)
    existing = {c.name for c in client.get_collections().collections}

    for col in COLLECTIONS:
        name = col["name"]
        if name in existing:
            print(f"  SKIP  {name} (already exists)")
            continue

        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=settings.qdrant_vector_dim,
                distance=Distance.COSINE,
            ),
        )
        print(f"  CREATED  {name}")

    print("\nDone. Collections:")
    for c in client.get_collections().collections:
        print(f"  - {c.name}")


if __name__ == "__main__":
    create_collections()
```

- [ ] **Step 2: Set QDRANT_VECTOR_DIM in .env**

Open `.env` and set `QDRANT_VECTOR_DIM` to match your embedding model's output dimension. For example, if using `nomic-embed-text`, set:

```bash
QDRANT_VECTOR_DIM=768
```

- [ ] **Step 3: Run the script (Docker services must be running)**

```bash
python scripts/create_collections.py
```

Expected output:
```
  CREATED  legal_docs
  CREATED  case_history
  CREATED  memory

Done. Collections:
  - legal_docs
  - case_history
  - memory
```

- [ ] **Step 4: Run again to verify idempotency**

```bash
python scripts/create_collections.py
```

Expected output:
```
  SKIP  legal_docs (already exists)
  SKIP  case_history (already exists)
  SKIP  memory (already exists)

Done. Collections:
  - legal_docs
  - case_history
  - memory
```

- [ ] **Step 5: Verify collections via Qdrant API**

```bash
curl -s http://localhost:6333/collections | python3 -m json.tool
```

Expected: JSON showing all 3 collections with correct vector size.

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/create_collections.py
git commit -m "feat: add create_collections script for Qdrant setup"
```

---

## Phase 1 Exit Criteria Verification

After completing all tasks, verify:

- [ ] `docker compose ps` shows Qdrant, Redis, Langfuse all running
- [ ] `python -c "from config import get_settings; s = get_settings(); print(s.qdrant_url)"` prints `http://localhost:6333`
- [ ] `pytest tests/ -v` — all tests pass
- [ ] `curl http://localhost:6333/collections` shows `legal_docs`, `case_history`, `memory`
- [ ] `redis-cli ping` returns `PONG`
- [ ] Langfuse accessible at `http://localhost:3000`
