"""Teable REST client (stdlib only).

Verified against Teable Community (self-hosted). Use the ``/api/...`` paths
below — the ``/api/v2/...`` variants 404 on community builds.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class ApiError(RuntimeError):
    pass


def api(method: str, teable_url: str, path: str, token: str, payload=None, params=None):
    """JSON request with 429-aware retries. Raises :class:`ApiError`."""
    url = teable_url + path
    if params:
        pairs = []
        for k, v in params.items():
            if isinstance(v, (list, tuple)):
                pairs += [(k, item) for item in v]
            else:
                pairs.append((k, v))
        qs = "&".join(
            f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in pairs
        )
        url += ("&" if "?" in url else "?") + qs
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    last = None
    for _attempt in range(6):
        try:
            req = urllib.request.Request(url, data=data, headers=hdr, method=method)
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace") if e.fp else ""
            if e.code == 429:
                last = f"HTTP 429 ({msg[:120]})"
                try:
                    time.sleep(int(e.headers.get("Retry-After", 3)))
                except (TypeError, ValueError):
                    time.sleep(3)
                continue
            last = f"HTTP {e.code} {msg[:300]}"
            if e.code in (400, 401, 403, 404, 409, 422):
                raise ApiError(last)
            time.sleep(2)
        except Exception as e:
            last = str(e)
            time.sleep(2)
    raise ApiError(f"{method} {path} failed: {last}")


# --- spaces / bases / tables / fields -----------------------------------------

def list_spaces(teable_url: str, token: str):
    return api("GET", teable_url, "/api/space", token)


def list_bases(teable_url: str, token: str):
    return api("GET", teable_url, "/api/base/access/all", token)


def create_base(teable_url: str, token: str, space_id: str, name: str):
    return api("POST", teable_url, "/api/base", token, {"spaceId": space_id, "name": name})


def delete_base(teable_url: str, token: str, base_id: str):
    return api("DELETE", teable_url, f"/api/base/{base_id}", token)


def list_tables(teable_url: str, token: str, base_id: str):
    d = api("GET", teable_url, f"/api/base/{base_id}/table/", token)
    return d if isinstance(d, list) else d.get("tables", d)


def create_table(teable_url: str, token: str, base_id: str, name: str, fields: list):
    return api(
        "POST", teable_url, f"/api/base/{base_id}/table/", token,
        {"name": name, "fields": fields},
    )


def list_fields(teable_url: str, token: str, table_id: str):
    d = api("GET", teable_url, f"/api/table/{table_id}/field", token)
    return d if isinstance(d, list) else d.get("fields", d)


def create_field(teable_url: str, token: str, table_id: str, payload: dict):
    return api("POST", teable_url, f"/api/table/{table_id}/field", token, payload)


def create_field_link(teable_url: str, token: str, table_id: str, name: str, foreign_table_id: str):
    # isOneWay=true: disables Teable's symmetric-field reconciliation, which
    # otherwise corrupts data when two tables share multiple many-many links
    # and both directions are populated from Airtable's dump.
    return create_field(
        teable_url, token, table_id,
        {"type": "link", "name": name,
         "options": {"relationship": "manyMany", "isOneWay": True,
                     "foreignTableId": foreign_table_id}},
    )


def create_field_multiselect(teable_url: str, token: str, table_id: str, name: str, choices: list):
    return create_field(
        teable_url, token, table_id,
        {"type": "multipleSelect", "name": name,
         "options": {"choices": [{"name": c} for c in choices]}},
    )


def delete_field(teable_url: str, token: str, table_id: str, field_id: str):
    api("DELETE", teable_url, f"/api/table/{table_id}/field/{field_id}", token)


# --- records -------------------------------------------------------------------

def list_all_records(teable_url: str, token: str, table_id: str):
    """Return all record ``ids`` (lightweight)."""
    ids, skip = [], 0
    while True:
        d = api("GET", teable_url, f"/api/table/{table_id}/record", token,
                params={"take": 1000, "skip": skip})
        recs = d.get("records", [])
        ids += [r["id"] for r in recs]
        if len(recs) < 1000:
            break
        skip += 1000
    return ids


def list_all_record_dicts(teable_url: str, token: str, table_id: str, field_key_type: str = "name"):
    """Return full record dicts (paged)."""
    recs, skip = [], 0
    while True:
        d = api("GET", teable_url, f"/api/table/{table_id}/record", token,
                params={"take": 1000, "skip": skip, "fieldKeyType": field_key_type})
        page = d.get("records", [])
        recs += page
        if len(page) < 1000:
            break
        skip += 1000
        time.sleep(config.TEABLE_SLEEP)
    return recs


def clear_seed_records(teable_url: str, token: str, table_id: str) -> None:
    """Delete Teable's auto-seeded placeholder rows (~3 empty rows per table)."""
    ids = list_all_records(teable_url, token, table_id)
    if ids:
        api("DELETE", teable_url, f"/api/table/{table_id}/record", token,
            params={"recordIds": ids})
        time.sleep(config.TEABLE_SLEEP)


def create_records(teable_url: str, token: str, table_id: str, records: list, batch: int = 100):
    """Bulk-create; returns created ids in input order."""
    ids = []
    for i in range(0, len(records), batch):
        chunk = records[i:i + batch]
        resp = api("POST", teable_url, f"/api/table/{table_id}/record", token,
                   {"fieldKeyType": "name", "typecast": True, "records": chunk})
        ids += [r["id"] for r in resp.get("records", [])]
        time.sleep(config.TEABLE_SLEEP)
    return ids


def update_records(teable_url: str, token: str, table_id: str, updates: list, batch: int = 100) -> int:
    """``updates``: list of ``(record_id, {field: value})``. Bulk PATCH."""
    total = 0
    for i in range(0, len(updates), batch):
        chunk = updates[i:i + batch]
        api("PATCH", teable_url, f"/api/table/{table_id}/record", token,
            {"records": [{"id": rid, "fields": fields} for rid, fields in chunk]})
        total += len(chunk)
        time.sleep(config.TEABLE_SLEEP)
    return total


# --- attachments (streaming upload, bounded RAM) --------------------------------

CHUNK = 1 << 20  # 1 MiB write chunks


def upload_attachment(teable_url: str, token: str, table_id: str,
                      record_id: str, field_id: str, filepath: str):
    """Stream a file into an attachment field; retries on 429/timeouts."""
    fname = os.path.basename(filepath)
    boundary = "----airtable-teable" + str(int(time.time() * 1000))
    size = os.path.getsize(filepath)
    head = (f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="file"; filename="{fname}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    path = f"/api/table/{table_id}/record/{record_id}/{field_id}/uploadAttachment"

    def build_conn():
        parts = urllib.parse.urlsplit(teable_url)
        if parts.scheme == "https":
            return http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=300)
        return http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=300)

    last = None
    for _attempt in range(6):
        try:
            conn = build_conn()
            conn.putrequest("POST", path)
            conn.putheader("Authorization", f"Bearer {token}")
            conn.putheader("Content-Length", str(len(head) + size + len(tail)))
            conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            conn.endheaders()
            conn.send(head)
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    conn.send(chunk)
            conn.send(tail)
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            if resp.status in (200, 201):
                return json.loads(body) if body else {}
            msg = body.decode("utf-8", "replace")[:300]
            last = f"HTTP {resp.status} {msg}"
            if resp.status == 429:
                try:
                    time.sleep(int(resp.getheader("Retry-After", "3")) or 3)
                except (TypeError, ValueError):
                    time.sleep(3)
                continue
            if resp.status in (400, 401, 403, 404, 409, 422):
                raise ApiError(last)
            time.sleep(2)
        except ApiError:
            raise
        except Exception as e:
            last = str(e)
            time.sleep(2)
    raise ApiError(f"upload {fname} failed: {last}")
