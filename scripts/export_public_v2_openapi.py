"""Write the deterministic public v2 OpenAPI contract used by SDK packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from joyhousebot.api.app import create_app
from joyhousebot.api.public_v2_openapi import public_v2_openapi_document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/api/public-v2.openapi.json"),
    )
    args = parser.parse_args()
    document = public_v2_openapi_document(create_app(surface="public").openapi())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
