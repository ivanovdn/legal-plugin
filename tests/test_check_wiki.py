"""scripts/check_wiki.py — the wiki's reference sections must list what the code has.

Built after the 2026-10-28 wiki audit found four live routes, seven state fields
and the app's own database service missing from the wiki: the "update the wiki"
rule named only the Shipped Log, so nothing else was ever updated.
"""
from scripts.check_wiki import (
    code_routes, compose_services, state_fields, store_tables, wiki_problems,
)

ROUTES = '''
from fastapi import APIRouter
router = APIRouter(prefix="/api")

@router.post("/query")
def submit(): ...

@router.get("/preferences")
def read(): ...

@router.put("/preferences")
def write(): ...
'''

HEALTH = '''
router = APIRouter()

@router.get("/health")
def health(): ...
'''

STATE = '''
class LegalAgentState(TypedDict):
    request: str
    turn_id: str
'''

DB = '''
CREATE TABLE IF NOT EXISTS audit_log (id BIGSERIAL);
CREATE TABLE IF NOT EXISTS turn_log (id BIGSERIAL);
'''

BASE = """
services:
  redis:
    image: redis
  app-db:
    image: postgres
"""

OVERLAY = """
services:
  backend:
    build: .
"""

WIKI = """# Wiki

## API Endpoints

| Method | Path | Status | Purpose |
|---|---|---|---|
| GET | `/health` | **Working** | Health check |
| POST | `/api/query` | **Working** | Submit |
| GET/PUT | `/api/preferences` | **Working** | Read / replace USER.md |
| GET | `/api/audit` | Not built | Query audit log |

## Data Models

### LegalAgentState (graph/state.py)

```python
request,          # the attorney's question
turn_id           # joins turn_log
```

## Docker Services (docker-compose.yml)

| Service | Port | Purpose |
|---|---|---|
| **redis** | 6379 | Checkpointer |
| **app-db** | 5434 | Tables `audit_log`, `turn_log` |

**VM overlay:** adds `backend`.

## Configuration
"""


def _problems(wiki=WIKI, routes=(ROUTES, HEALTH), state=STATE, base=BASE, overlay=OVERLAY, db=DB):
    return wiki_problems(
        wiki,
        routes=code_routes(routes),
        fields=state_fields(state),
        base_services=compose_services(base),
        overlay_services=compose_services(overlay),
        tables=store_tables(db),
    )


def test_code_routes_join_the_router_prefix():
    assert code_routes((ROUTES, HEALTH)) == {
        ("POST", "/api/query"), ("GET", "/api/preferences"),
        ("PUT", "/api/preferences"), ("GET", "/health"),
    }


def test_state_fields_are_the_typeddict_keys():
    assert state_fields(STATE) == {"request", "turn_id"}


def test_store_tables_are_the_created_tables():
    assert store_tables(DB) == {"audit_log", "turn_log"}


def test_store_tables_ignore_the_phrase_inside_a_comment():
    # memory/db.py has exactly this comment; it once read as a table called "will".
    db = DB + "    # CREATE above because CREATE TABLE IF NOT EXISTS will not add a column\n"
    assert store_tables(db) == {"audit_log", "turn_log"}


def test_a_wiki_that_matches_the_code_has_no_problems():
    assert _problems() == []


def test_a_route_the_wiki_does_not_list_is_a_problem():
    routes = ROUTES + '\n@router.post("/events")\ndef events(): ...\n'
    assert _problems(routes=(routes, HEALTH)) == [
        "route POST /api/events is not in the wiki's API Endpoints table",
    ]


def test_a_working_row_with_no_route_is_a_problem():
    wiki = WIKI.replace(
        "| GET | `/api/audit` | Not built |", "| GET | `/api/audit` | **Working** |"
    )
    assert _problems(wiki=wiki) == [
        "the wiki lists GET /api/audit as built, but no route serves it",
    ]


def test_a_combined_method_row_must_cover_each_method():
    wiki = WIKI.replace("| GET/PUT | `/api/preferences` |", "| GET | `/api/preferences` |")
    assert _problems(wiki=wiki) == [
        "route PUT /api/preferences is not in the wiki's API Endpoints table",
    ]


def test_a_route_the_wiki_calls_not_built_is_a_problem():
    wiki = WIKI.replace(
        "| POST | `/api/query` | **Working** |", "| POST | `/api/query` | Not built |"
    )
    assert _problems(wiki=wiki) == [
        "route POST /api/query is not in the wiki's API Endpoints table",
    ]


def test_state_fields_missing_from_or_extra_in_the_wiki_are_problems():
    wiki = WIKI.replace("turn_id           # joins turn_log", "trace_ref         # gone")
    assert _problems(wiki=wiki) == [
        "state field turn_id is not in the wiki's LegalAgentState block",
        "the wiki's LegalAgentState block lists trace_ref, which graph/state.py does not have",
    ]


def test_a_compose_service_missing_from_or_extra_in_the_table_is_a_problem():
    wiki = WIKI.replace("| **redis** | 6379 | Checkpointer |", "| **qdrant** | 6333 | Vectors |")
    assert _problems(wiki=wiki) == [
        "compose service redis is not in the wiki's Docker Services table",
        "the wiki's Docker Services table lists qdrant, which docker-compose.yml does not define",
    ]


def test_an_overlay_service_must_be_named_in_the_docker_section():
    wiki = WIKI.replace("**VM overlay:** adds `backend`.", "")
    assert _problems(wiki=wiki) == [
        "VM overlay service backend is not named in the wiki's Docker Services section",
    ]


def test_a_store_table_the_wiki_does_not_name_is_a_problem():
    db = DB + "CREATE TABLE IF NOT EXISTS feedback (id BIGSERIAL);\n"
    assert _problems(db=db) == [
        "store table feedback is not named in the wiki's Docker Services section (the app-db row)",
    ]


def test_a_missing_section_is_a_problem_not_a_crash():
    wiki = WIKI.replace("## API Endpoints", "## Endpoints")
    problems = _problems(wiki=wiki)
    assert "the wiki has no '## API Endpoints' section" in problems
