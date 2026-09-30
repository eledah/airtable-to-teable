"""Shared configuration: env vars, token/URL resolution, tuning constants."""

from __future__ import annotations

import os

# --- Endpoints ----------------------------------------------------------------
AIRTABLE_API = "https://api.airtable.com/v0"
DEFAULT_TEABLE_URL = os.environ.get("TEABLE_API_URL", "http://localhost:3000").rstrip("/")

# --- Rate limits / batching ----------------------------------------------------
AIRTABLE_RATE_SLEEP = float(os.environ.get("AIRTABLE_RATE_SLEEP", "0.3"))
TEABLE_SLEEP = float(os.environ.get("TEABLE_SLEEP", "0.15"))
TEABLE_BATCH = int(os.environ.get("TEABLE_BATCH", "100"))
ATTACH_BATCH = int(os.environ.get("TEABLE_ATTACH_BATCH", "50"))

# --- Retry policy (Airtable downloader) ----------------------------------------
MAX_ATTEMPTS = int(os.environ.get("AIRTABLE_MAX_ATTEMPTS", "16"))
BASE_BACKOFF = float(os.environ.get("AIRTABLE_BASE_BACKOFF", "1.5"))
MAX_BACKOFF = float(os.environ.get("AIRTABLE_MAX_BACKOFF", "60"))
MAX_TOTAL_WAIT = float(os.environ.get("AIRTABLE_MAX_TOTAL_WAIT", "600"))

# --- Orchestrator guards --------------------------------------------------------
MIN_FREE_KB = int(os.environ.get("MIGRATE_MIN_FREE_KB", str(400_000)))  # 400 MB
SAFETY_KB = int(os.environ.get("MIGRATE_SAFETY_KB", str(300 * 1024)))  # 300 MB


def resolve_airtable_token(cli_value: str = "") -> str:
    """Priority: --token flag > AIRTABLE_TOKEN env > AIRTABLE_PAT env."""
    if cli_value:
        return cli_value.strip()
    for var in ("AIRTABLE_TOKEN", "AIRTABLE_PAT"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return ""


def resolve_teable_token(cli_value: str = "", token_file: str = "") -> str:
    """Priority: --token flag > TEABLE_TOKEN env > --token-file / default file."""
    if cli_value:
        return cli_value.strip()
    env_val = os.environ.get("TEABLE_TOKEN", "").strip()
    if env_val:
        return env_val
    candidates = []
    if token_file:
        candidates.append(token_file)
    candidates.append(os.environ.get("TEABLE_TOKEN_FILE", ""))
    # Legacy default: .teable_token next to the caller's CWD (never committed).
    candidates.append(os.path.join(os.getcwd(), ".teable_token"))
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    val = f.read().strip()
                if val:
                    return val
            except OSError:
                continue
    return ""


def resolve_teable_url(cli_value: str = "") -> str:
    """Priority: --teable-url flag > TEABLE_API_URL env > built-in default."""
    if cli_value:
        return cli_value.strip().rstrip("/")
    return os.environ.get("TEABLE_API_URL", DEFAULT_TEABLE_URL).strip().rstrip("/") or DEFAULT_TEABLE_URL
