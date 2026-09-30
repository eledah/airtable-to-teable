"""Stage 4 — orchestrator: import -> attach -> prune, smallest-first, resumable.

For every base that still has a source folder in the backup root:
  * not in Teable  -> run the importer, then the attachment loader + prune
  * in Teable      -> run the attachment loader + prune (or prune directly
                      when there are no attachments)

A base is recorded in the state file only after its source folder is gone.
``add_attachments --prune`` is idempotent, so interrupted runs resume safely.
Disk guards stop/defer work before the volume fills.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from . import config


def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Logger:
    def __init__(self, path: str):
        self.path = path

    def log(self, msg: str) -> None:
        line = f"[{now_ts()}] {msg}"
        print(line, flush=True)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def free_disk_kb(path: str = "/") -> int:
    st = os.statvfs(path)
    return (st.f_bavail * st.f_frsize) // 1024


def dir_kb(path: str) -> int:
    total = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total // 1024


def data_kb(bdir: str) -> int:
    """CSV (data) footprint only — attachments relocate bytes, data consumes."""
    total = 0
    try:
        for f in os.listdir(bdir):
            fp = os.path.join(bdir, f)
            if os.path.isfile(fp) and f.endswith(".csv"):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total // 1024


def load_manifest_names(out: str) -> dict:
    """``{folder_basename: real_base_name}`` (+ identity entries)."""
    path = os.path.join(out, "manifest.json")
    m: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
            for e in json.load(f):
                bdir = os.path.basename(e.get("dir") or "")
                bname = e.get("base")
                if bdir and bname:
                    m[bdir] = bname
            for bdir, bname in list(m.items()):
                m.setdefault(bname, bname)
    except (OSError, ValueError):
        pass
    return m


def teable_base_names(teable_url: str, token: str) -> set:
    import urllib.request
    req = urllib.request.Request(
        teable_url + "/api/base/access/all",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return {x["name"] for x in data}


def load_state(path: str) -> set:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return set(json.load(f))
        except (OSError, ValueError):
            return set()
    return set()


def save_state(path: str, names: set) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(names), f, ensure_ascii=False)
    os.replace(tmp, path)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Orchestrate the remaining migration (import+attach+prune)")
    ap.add_argument("--out", default="", help="backup root (default: $BACKUP_DIR or ./backup)")
    ap.add_argument("--state", default="", help="state file (default: ./migration_state.json)")
    ap.add_argument("--log", default="", help="run log (default: ./migration_run.log)")
    ap.add_argument("--token", default="", help="Teable PAT (default: $TEABLE_TOKEN)")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--teable-url", default="", help="Teable base URL (default: $TEABLE_API_URL)")
    ap.add_argument("--space", default="", help="Teable spaceId (default: first space)")
    ap.add_argument("--min-free-kb", type=int, default=config.MIN_FREE_KB)
    ap.add_argument("--dry-run", action="store_true", help="print plan only")
    return ap


def _run(log: Logger, cmd: list) -> bool:
    log.log("RUN: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        log.log("   | " + line)
    return proc.returncode == 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out = args.out or os.environ.get("BACKUP_DIR", os.path.join(os.getcwd(), "backup"))
    state_path = args.state or os.path.join(os.getcwd(), "migration_state.json")
    log_path = args.log or os.path.join(os.getcwd(), "migration_run.log")
    log = Logger(log_path)

    teable_url = config.resolve_teable_url(args.teable_url)
    token = config.resolve_teable_token(args.token, args.token_file)
    if not token and not args.dry_run:
        print("error: no Teable token (pass --token or set $TEABLE_TOKEN)")
        return 2
    if not os.path.isdir(out):
        print(f"error: backup root not found: {out}")
        return 2

    done = load_state(state_path)
    log.log(f"Migration orchestrator start. Already done: {len(done)} (Teable: {teable_url})")
    teable_names: set = set()
    if not args.dry_run:
        teable_names = teable_base_names(teable_url, token)
    manifest_names = load_manifest_names(out)

    pending = []
    for d in sorted(os.listdir(out), key=str.lower):
        bdir = os.path.join(out, d)
        if not os.path.isdir(bdir) or d in done:
            continue
        bname = manifest_names.get(d, d)
        needs_attach = os.path.isdir(os.path.join(bdir, d + "_attachments"))
        pending.append({
            "folder": d, "base": bname,
            "size_kb": dir_kb(bdir), "data_kb": data_kb(bdir),
            "in_teable": bname in teable_names, "has_att": needs_attach,
        })
    pending.sort(key=lambda x: x["size_kb"])
    log.log(f"Plan: {len(pending)} bases pending "
            f"({sum(1 for p in pending if p['in_teable'])} in Teable, "
            f"{sum(1 for p in pending if not p['in_teable'])} need import). "
            f"Free disk: {free_disk_kb() // 1024} MB.")
    if args.dry_run:
        for p in pending:
            log.log(f"  [dry] {p['base']} ({p['size_kb'] / 1024:.1f} MB, "
                    f"in_teable={p['in_teable']}, has_att={p['has_att']})")
        return 0

    for i, p in enumerate(pending, 1):
        bdir = os.path.join(out, p["folder"])
        log.log(f"\n===== [{i}/{len(pending)}] {p['base']} "
                f"({p['size_kb'] / 1024:.0f} MB source / {p['data_kb'] / 1024:.1f} MB data, "
                f"in_teable={p['in_teable']}) =====")
        if not os.path.isdir(bdir):
            done.add(p["base"]); save_state(state_path, done)
            continue
        if free_disk_kb() < args.min_free_kb:
            log.log(f"!! FREE DISK {free_disk_kb() // 1024}MB < guard "
                    f"{args.min_free_kb // 1024}MB — stopping.")
            break
        if p["data_kb"] > free_disk_kb() - config.SAFETY_KB:
            log.log(f"   data {p['data_kb'] / 1024:.1f}MB won't fit "
                    f"(free {free_disk_kb() // 1024}MB) — deferring {p['base']}.")
            continue
        try:
            try:
                csvs = [f for f in os.listdir(bdir)
                        if f.endswith(".csv") and os.path.isfile(os.path.join(bdir, f))]
            except OSError:
                csvs = []
            if not csvs and not p["has_att"]:
                shutil.rmtree(os.path.join(out, p["folder"]), ignore_errors=True)
                done.add(p["base"]); save_state(state_path, done)
                log.log(f"✓ {p['base']}: empty placeholder pruned.")
                continue
            if not p["in_teable"]:
                cmd = [sys.executable, "-m", "airtable_teable.import_data",
                       "--out", out, "--bases", p["base"],
                       "--teable-url", teable_url]
                if args.space:
                    cmd += ["--space", args.space]
                ok = _run(log, cmd)
                teable_names = teable_base_names(teable_url, token)
                p["in_teable"] = ok and p["base"] in teable_names
                if not p["in_teable"]:
                    log.log(f"!! import did not create base '{p['base']}'; deferring.")
                    continue
            if p["has_att"]:
                ok = _run(log, [sys.executable, "-m", "airtable_teable.attachments",
                                "--out", out, "--bases", p["base"], "--prune",
                                "--teable-url", teable_url])
            else:
                ok = True
                shutil.rmtree(os.path.join(out, p["folder"]), ignore_errors=True)
            bdir = os.path.join(out, p["folder"])
            if ok and not os.path.isdir(bdir):
                done.add(p["base"])
                save_state(state_path, done)
                log.log(f"✓ {p['base']} migrated & pruned.")
            else:
                log.log(f"? {p['base']} finished but folder still present; leaving incomplete.")
        except Exception as e:
            log.log(f"!! EXCEPTION {p['base']}: {e}")
    log.log(f"\nMigration orchestrator pass complete. Done: {len(done)}. "
            f"Free disk: {free_disk_kb() // 1024} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
