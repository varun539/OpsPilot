"""OpsPilot API application entrypoint."""

from fastapi import FastAPI

app = FastAPI(
    title="OpsPilot API",
    description="Agentic AI Support and Incident Investigation System",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used to confirm the service is up."""
    return {"status": "ok", "service": "opspilot-api"}
