#!/usr/bin/env python3
"""Small unauthenticated, read-only Paperclip HTTP probe; no install or login.

Response-body budget is 2,000,000 bytes across requests. Never reads credentials,
environment API keys, browser state, or project configuration. Saves diagnostic
metadata and bounded API error bodies, not article content.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request


BASE = "https://paperclip.gxl.ai"
BUDGET = 2_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/PAPERCLIP_PROBE.json"))
    args = parser.parse_args()
    requests = [
        ("installer_text", "/install.sh", None, 150_000),
        ("openapi", "/api/v1/openapi.json", None, 500_000),
        ("pmc_search", "/api/v1/search", {
            "query": "case report", "sources": ["pmc"],
            "num_results": 1, "ranking": "bm25", "expand": False,
        }, 50_000),
        ("pmc_document", "/api/v1/documents/PMC8654144", None, 50_000),
    ]
    report = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "authentication": "none; no credential/environment reads",
        "response_body_budget": BUDGET,
        "scope": "Public installer/schema GET; read-only search POST and document GET",
        "requests": [],
    }
    total = 0
    for name, route, payload, per_request_cap in requests:
        cap = min(per_request_cap, BUDGET - total)
        if cap <= 0:
            break
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(BASE + route, data=data, headers={
            "User-Agent": "OpenPatients2-PaperclipEvaluation/1.0",
            "Accept": "application/json, text/plain;q=0.9",
            **({"Content-Type": "application/json"} if data else {}),
        })
        item = {"name": name, "url": BASE + route,
                "method": req.get_method(), "payload": payload}
        try:
            try:
                response = urllib.request.urlopen(req, timeout=20)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                # Read at most cap bytes, then close. A cap hit is conservative.
                body = response.read(cap)
                total += len(body)
                item.update(status=response.code, final_url=response.url,
                            bytes_read=len(body), cap_reached=len(body) == cap,
                            sha256=hashlib.sha256(body).hexdigest(),
                            content_type=response.headers.get("Content-Type"))
                decoded = body.decode("utf-8", errors="replace")
                if name == "installer_text":
                    # Inspect shell as text, never execute it or fetch its artifacts.
                    item["source_references"] = [line.strip() for line in decoded.splitlines()
                        if any(token in line for token in (
                            "https://", "CLI_URL", "SDK_URL", "curl", "wget", ".py", "/api/"
                        ))][:60]
                elif name == "openapi" and response.code == 200:
                    schema = json.loads(decoded)
                    item["schema"] = schema
                else:
                    item["body"] = decoded[:4_000]
        except Exception as exc:
            item["transport_error"] = f"{type(exc).__name__}: {exc}"
        report["requests"].append(item)
    report["total_response_body_bytes"] = total
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "bytes": total,
                      "requests": [{k: v for k, v in item.items()
                                    if k in ("name", "status", "bytes_read", "transport_error")}
                                   for item in report["requests"]]}, indent=2))


if __name__ == "__main__":
    main()
