#!/usr/bin/env python3
"""Export the OpsPilot FastAPI OpenAPI schema for frontend type generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

def main() -> None:
    from backend.app.main import create_app

    output = project_root / "frontend" / "openapi.json"
    schema = create_app().openapi()
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
