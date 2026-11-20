"""ASGI application used for browser end-to-end tests.

This harness is test-only. It never runs in production and is only started by
Playwright (or by a developer manually) with explicit SQLite paths in /tmp.
External model providers are replaced with deterministic fakes so the real
LangGraph graph, diagnostic tools, persistence, interrupt, and resume paths are
exercised without live network calls.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from backend.app.agent.models import InvestigationPlan
from backend.app.auth.config import AuthSettings
from backend.app.main import create_app
from backend.app.rag.models import InvestigationReport

EVIDENCE_CHUNK_ID = "kb-e2e-1"


class FakeProvider:
    """Deterministic structured-output provider for planner and report nodes."""

    model = "fake-deterministic"

    def generate_structured(
        self,
        *,
        system_instructions,
        user_input,
        output_schema,
        timeout=None,
        max_output_tokens=None,
    ):
        if output_schema.__name__ == "InvestigationPlan":
            output = InvestigationPlan(
                selected_tools=["log_analyzer"], rationale="E2E deterministic plan"
            )
        else:
            output = InvestigationReport(
                summary=(
                    "E2E investigation identified elevated checkout latency "
                    "tied to pooled connections."
                ),
                incident_category="database",
                affected_components=["checkout-api", "postgres"],
                evidence_status="sufficient",
                root_cause_hypotheses=[
                    {
                        "title": "Database connection pool exhaustion",
                        "explanation": (
                            "Retrieved runbook evidence links checkout latency "
                            "to exhausted pooled connections."
                        ),
                        "supporting_chunk_ids": [EVIDENCE_CHUNK_ID],
                        "evidence_strength": "medium",
                    }
                ],
                supporting_evidence=[
                    {
                        "chunk_id": EVIDENCE_CHUNK_ID,
                        "doc_id": "doc-e2e-1",
                        "title": "Checkout latency runbook",
                        "reason": "Documents connection pool exhaustion symptoms.",
                    }
                ],
                recommended_actions=[
                    {
                        "description": (
                            "Raise the checkout connection pool ceiling and "
                            "verify saturation."
                        ),
                        "rationale": "Pool exhaustion is the leading hypothesis.",
                        "supporting_chunk_ids": [EVIDENCE_CHUNK_ID],
                        "risk_level": "medium",
                        "requires_human_review": True,
                    }
                ],
                missing_information=["Database metrics for the incident window"],
                limitations=["Synthetic end-to-end evidence only"],
            )
        result = MagicMock()
        result.output = output
        result.usage = None
        result.model = "fake-deterministic"
        return result


class FakeRetrievalEngine:
    """Deterministic knowledge retrieval with a stable chunk ID."""

    def search(self, query: str, top_k: int = 5, filters=None):
        return [
            {
                "chunk_id": EVIDENCE_CHUNK_ID,
                "doc_id": "doc-e2e-1",
                "title": "Checkout latency runbook",
                "source_path": "runbooks/checkout-latency.md",
                "chunk_text": (
                    "Checkout latency and 5xx responses correlate with exhausted "
                    "database connection pools. Inspect pool saturation and slow queries."
                ),
                "metadata": {"section": "diagnosis"},
                "final_rank": 1,
            }
        ][:top_k]


def _remove(path: str | None) -> None:
    if not path:
        return
    base = Path(path)
    for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        candidate.unlink(missing_ok=True)


app_db = os.getenv("OPSPILOT_E2E_APP_DB", "/tmp/opspilot-e2e-app.sqlite")
checkpoint_db = os.getenv(
    "OPSPILOT_E2E_CHECKPOINT_DB", "/tmp/opspilot-e2e-checkpoints.sqlite"
)
if os.getenv("OPSPILOT_E2E_PRESERVE") != "1":
    _remove(app_db)
    _remove(checkpoint_db)

app = create_app(
    db_path=app_db,
    checkpoint_db_path=checkpoint_db,
    auth_settings=AuthSettings(
        host="127.0.0.1",
        auth_mode="development",
        dev_auth_enabled=True,
        cors_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    ),
    llm_provider=FakeProvider(),
    retrieval_engine=FakeRetrievalEngine(),
)
