"""Container entry point for one bounded file-to-JSON extraction job."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from porthouse.extension_sdk.network import sanitize_error_message

from .ingest import source_parsers
from .ingest.source_parsers import SourceParseError


async def _extract(request: dict[str, Any], body: bytes) -> dict[str, Any]:
    source_parsers.MAX_DOCUMENT_PAGES = max(1, min(int(request["max_pages"]), 200))
    source_parsers.MAX_PARSED_CHARS = max(1_000, min(int(request["max_chars"]), 500_000))

    async def load_input_asset(asset_id: str) -> dict[str, Any]:
        if asset_id != request["asset_id"]:
            raise PermissionError("input asset identity mismatch")
        return {
            "body": body,
            "display_name": request["display_name"],
            "media_type": request["media_type"],
        }

    try:
        parsed = await source_parsers.default_source_parser_registry().parse_snapshot(
            {
                "source_type": "file",
                "attachments": [
                    {
                        "reference_kind": "runtime_input",
                        "asset_id": request["asset_id"],
                        "display_name": request["display_name"],
                        "media_type": request["media_type"],
                    }
                ],
            },
            input_asset_loader=load_input_asset,
        )
    except SourceParseError as exc:
        return {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": sanitize_error_message(str(exc)),
                "retryable": exc.retryable,
                "parser_id": exc.parser_id,
                "parser_version": exc.parser_version,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "PARSER_FAILED",
                "message": sanitize_error_message(str(exc)),
                "retryable": False,
                "parser_id": "unresolved",
                "parser_version": "1",
            },
        }
    return {
        "ok": True,
        "parser_id": parsed.parser_id,
        "parser_version": parsed.parser_version,
        "chunks": parsed.chunks,
        "trace": parsed.trace,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    result = asyncio.run(_extract(request, Path(args.input).read_bytes()))
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
