"""Airtable REST helpers: retrying GET, schema + record paging (stdlib only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config

API = config.AIRTABLE_API


def http_get(url: str, token: str, binary: bool = False):
    """GET with exponential backoff on 429 / 5xx / network errors.

    Honors ``Retry-After`` (capped), backs off ``BASE_BACKOFF * 2^n`` with
    jitter, gives up after ``MAX_ATTEMPTS`` or ``MAX_TOTAL_WAIT``. Returns
    ``bytes`` when ``binary`` else ``str``; returns ``None`` on 404 (JSON).
    """
    hdr = {"Authorization": f"Bearer {token}"}
    if not binary:
        hdr["Accept"] = "application/json"
    last = None
    total_slept = 0.0
    for attempt in range(config.MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            return data if binary else data.decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404 and not binary:
                return None
            if e.code == 429:
                try:
                    wait = int(e.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    wait = 0
                last = f"HTTP 429 rate-limited (attempt {attempt + 1}/{config.MAX_ATTEMPTS})"
            elif 500 <= e.code < 600:
                wait = 0
                last = f"HTTP {e.code} server error (attempt {attempt + 1}/{config.MAX_ATTEMPTS})"
            else:
                last = f"HTTP {e.code}"
                wait = 0
            if wait == 0:
                wait = config.BASE_BACKOFF * (2**attempt) * (0.5 + 0.5 * (attempt % 3) / 3)
                wait = min(wait, config.MAX_BACKOFF)
        except Exception as e:  # network / timeout / ssl
            last = str(e)
            wait = config.BASE_BACKOFF * (2**attempt) * (0.5 + 0.5 * (attempt % 3) / 3)
            wait = min(wait, config.MAX_BACKOFF)
        if total_slept + wait > config.MAX_TOTAL_WAIT:
            raise RuntimeError(f"GET failed {url}: {last}")
        time.sleep(wait)
        total_slept += wait
    raise RuntimeError(f"GET failed {url}: {last}")


def get_json(url: str, token: str):
    raw = http_get(url, token)
    if raw is None:
        return None
    return json.loads(raw)


def list_bases(token: str) -> list:
    d = get_json(f"{API}/meta/bases", token)
    return (d or {}).get("bases", [])


def list_workspaces(token: str) -> dict:
    """Best-effort ``{base_id: workspace_name}``; ``{}`` if not permitted."""
    try:
        d = get_json(f"{API}/meta/workspaces", token)
    except RuntimeError:
        return {}
    if not d or "workspaces" not in d:
        return {}
    mapping = {}
    for ws in d["workspaces"]:
        wname = ws.get("name", "Workspace")
        for b in ws.get("bases", []):
            mapping[b["id"]] = wname
    return mapping


def get_schema(token: str, base_id: str) -> list:
    d = get_json(f"{API}/meta/bases/{base_id}/tables", token)
    return (d or {}).get("tables", [])


def page_records(token: str, base_id: str, table_id: str):
    """Yield raw Airtable record dicts (100/page, paced to 5 req/s)."""
    offset = None
    while True:
        url = f"{API}/{base_id}/{table_id}?pageSize=100"
        if offset:
            url += f"&offset={offset}"
        d = get_json(url, token)
        if d is None:
            return
        for rec in d.get("records", []):
            yield rec
        offset = d.get("offset")
        if not offset:
            break
        time.sleep(config.AIRTABLE_RATE_SLEEP)
