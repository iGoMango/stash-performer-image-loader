#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assign local images to StashApp performers using safe fuzzy name matching, or
create performers whose exact normalized names do not already exist.
Auto-detects whether StashApp expects image Upload or String and falls back.

Usage:
  - Put config in .env (see .env.example)
  - .venv/bin/python scripts/find_custom_performers_pfp.py
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv
from rapidfuzz import process, fuzz

# Load the project's .env before reading any settings. Do not depend on
# the current working directory or overwrite explicitly exported variables.
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = (SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR) / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False, interpolate=False)

def getenv_bool(k: str, default: bool) -> bool:
    v = os.getenv(k)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")

STASH_URL      = os.getenv("STASH_URL", "http://localhost:9999/graphql")
API_KEY        = os.getenv("API_KEY", "")
IMG_DIR        = os.getenv("IMG_DIR", ".")
try:
    FUZZ_THRESHOLD = int(os.getenv("FUZZ_THRESHOLD", "75"))
except ValueError:
    print("[ERR] FUZZ_THRESHOLD must be an integer.", file=sys.stderr)
    raise SystemExit(2)
DRY_RUN        = getenv_bool("DRY_RUN", False)
MIN_PARTIAL_LENGTH = 4
MIN_SCORE_MARGIN = 5

HEADERS = {"ApiKey": API_KEY} if API_KEY else {}

FIND_PAGE = """
query FindPerformers($filter: FindFilterType!) {
  findPerformers(filter: $filter) {
    count
    performers { id name }
  }
}
"""

UPDATE_PERFORMER = """
mutation PerformerUpdate($id: ID!, $image: Upload!) {
  performerUpdate(input: { id: $id, image: $image }) { id name }
}
"""

UPDATE_PERFORMER_STRING = """
mutation PerformerUpdateString($id: ID!, $image: String!) {
  performerUpdate(input: { id: $id, image: $image }) { id name }
}
"""

CREATE_PERFORMER_STRING = """
mutation PerformerCreate($name: String!, $image: String!) {
  performerCreate(input: { name: $name, image: $image }) { id name }
}
"""

def gq(query, variables=None):
    r = requests.post(STASH_URL, json={"query": query, "variables": variables or {}}, headers=HEADERS, timeout=60)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Non-JSON response {r.status_code}: {r.text[:500]}")
    if r.status_code != 200 or "errors" in j:
        raise RuntimeError(f"GraphQL error {r.status_code}: {json.dumps(j, ensure_ascii=False)[:800]}")
    return j["data"]

def load_all_performers(per=200):
    page, names, total = 1, [], None
    while True:
        data = gq(FIND_PAGE, {"filter": {"page": page, "per_page": per}})
        fp = data["findPerformers"]
        if total is None: total = fp["count"]
        perf = fp.get("performers") or []
        if not perf: break
        for p in perf:
            pid = p["id"]
            nm = (p.get("name") or "").strip()
            if nm: names.append((nm, pid))
        if page * per >= (total or 0): break
        page += 1
    return names

# ---- matching helpers ----
def normalize_basename(basename: str) -> str:
    # e.g. 'alyssa..sanchez_0_abc..._avatar' -> 'alyssa..sanche'
    base = re.sub(r"z?_0_.*$", "", basename, flags=re.IGNORECASE)
    base = base.strip(" ._")
    base = re.sub(r"_{2,}", "_", base)
    base = re.sub(r"\s{2,}", " ", base)
    return base

def normalize_for_match(s: str) -> str:
    # treat ., _, and spaces as separators; lowercase
    s = s.lower()
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def mime_for(path: str) -> str:
    m, _ = mimetypes.guess_type(path)
    return m or "image/jpeg"

def find_best_match(candidates, keys):
    """Return a unique match, or a reason why the filename is unsafe to match."""
    exact = {
        idx: cand
        for cand in candidates
        for idx, key in enumerate(keys)
        if cand == key
    }
    if len(exact) == 1:
        idx, cand = next(iter(exact.items()))
        return (100.0, idx, cand), None
    if len(exact) > 1:
        return None, "multiple performers have the same normalized name"

    scores = {}
    for cand in candidates:
        compact = re.sub(r"\W", "", cand)
        if len(compact) < MIN_PARTIAL_LENGTH:
            continue
        for _, score, idx in process.extract(
            cand, keys, scorer=fuzz.token_set_ratio, limit=2
        ):
            previous = scores.get(idx)
            if previous is None or score > previous[0]:
                scores[idx] = (score, cand)

    ranked = sorted(
        ((score, idx, cand) for idx, (score, cand) in scores.items()),
        reverse=True,
    )
    if not ranked:
        return None, f"partial name is shorter than {MIN_PARTIAL_LENGTH} characters"
    if ranked[0][0] < FUZZ_THRESHOLD:
        return None, f"best score {ranked[0][0]:.1f} is below {FUZZ_THRESHOLD}"
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MIN_SCORE_MARGIN:
        return None, (
            f"ambiguous top scores {ranked[0][0]:.1f} and {ranked[1][0]:.1f} "
            f"(minimum margin is {MIN_SCORE_MARGIN})"
        )
    return ranked[0], None

# ---- upload mode (auto) ----
class UploadMode:
    AUTO = "auto"
    UPLOAD = "upload"
    STRING = "string"

upload_mode = UploadMode.AUTO

def send_upload(performer_id: str, image_path: str):
    ops = {"query": UPDATE_PERFORMER.replace("\n", " "),
           "variables": {"id": performer_id, "image": None}}
    with open(image_path, "rb") as image_file:
        files = {
            "operations": (None, json.dumps(ops), "application/json"),
            "map": (None, '{"0": ["variables.image"]}'),
            "0": (os.path.basename(image_path), image_file, mime_for(image_path)),
        }
        return requests.post(STASH_URL, files=files, headers=HEADERS, timeout=120)

def send_string(performer_id: str, image_path: str):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    data_uri = f"data:{mime_for(image_path)};base64,{b64}"
    payload = {"query": UPDATE_PERFORMER_STRING, "variables": {"id": performer_id, "image": data_uri}}
    return requests.post(STASH_URL, json=payload, headers=HEADERS, timeout=120)

def create_performer(name: str, image_path: str):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    data_uri = f"data:{mime_for(image_path)};base64,{b64}"
    payload = {
        "query": CREATE_PERFORMER_STRING,
        "variables": {"name": name, "image": data_uri},
    }
    return requests.post(STASH_URL, json=payload, headers=HEADERS, timeout=120)

def upload_avatar_auto(performer_id: str, image_path: str):
    global upload_mode
    if upload_mode == UploadMode.UPLOAD:
        return send_upload(performer_id, image_path)
    if upload_mode == UploadMode.STRING:
        return send_string(performer_id, image_path)

    # AUTO: try Upload first
    resp = send_upload(performer_id, image_path)
    try:
        js = resp.json()
    except Exception:
        js = {"status": resp.status_code, "text": resp.text[:200]}
    if resp.ok and "errors" not in js:
        upload_mode = UploadMode.UPLOAD
        return resp

    err_text = json.dumps(js, ensure_ascii=False).lower()
    if "string" in err_text and "upload" in err_text:
        upload_mode = UploadMode.STRING
        return send_string(performer_id, image_path)

    return resp

def parse_args():
    parser = argparse.ArgumentParser(
        description="Match local images to StashApp performers and set their photos."
    )
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help=(
            "Create a performer from each image filename only when that exact "
            "normalized name is absent. Existing performers are not modified. "
            "DRY_RUN still prevents all changes."
        ),
    )
    return parser.parse_args()

def run_create_missing(images, pairs):
    existing_names = {}
    for name, performer_id in pairs:
        existing_names.setdefault(normalize_for_match(name), []).append(
            (name, performer_id)
        )

    planned = []
    for fname in images:
        image_path = os.path.join(IMG_DIR, fname)
        performer_name = os.path.splitext(fname)[0].strip()
        normalized_name = normalize_for_match(performer_name)
        if not normalized_name:
            print(f"[SKIP] Image has no usable performer name: {fname}")
            continue

        existing = existing_names.get(normalized_name)
        if existing:
            labels = ", ".join(f"'{name}' ({pid})" for name, pid in existing)
            print(f"[EXISTS] {fname} → {labels}")
            continue

        planned.append((fname, image_path, performer_name, normalized_name))

    creation_counts = Counter(item[3] for item in planned)
    created_count = 0
    would_create_count = 0
    skipped_count = len(images) - len(planned)
    error_count = 0

    for fname, image_path, performer_name, normalized_name in planned:
        if creation_counts[normalized_name] > 1:
            conflicts = ", ".join(
                item[0] for item in planned if item[3] == normalized_name
            )
            print(
                f"[SKIP] {fname} :: multiple images would create the same "
                f"performer '{performer_name}': {conflicts}"
            )
            skipped_count += 1
            continue

        if DRY_RUN:
            print(f"[DRY-CREATE] {fname} → new performer '{performer_name}'")
            would_create_count += 1
            continue

        try:
            resp = create_performer(performer_name, image_path)
        except (OSError, requests.RequestException) as exc:
            print(f"[ERR] Could not create '{performer_name}' from {fname}: {exc}")
            error_count += 1
            continue

        try:
            js = resp.json()
        except Exception:
            js = {"status": resp.status_code, "text": resp.text[:200]}

        if resp.ok and "errors" not in js:
            created = (js.get("data") or {}).get("performerCreate") or {}
            created_count += 1
            print(
                f"[CREATED] {fname} → '{created.get('name', performer_name)}' "
                f"({created.get('id', 'unknown id')})"
            )
        else:
            error_count += 1
            print(
                f"[ERR] Could not create '{performer_name}' from {fname} :: "
                f"{json.dumps(js, ensure_ascii=False)[:500]}"
            )

    action = "would_create" if DRY_RUN else "created"
    print(
        f"[SUMMARY] {action}={would_create_count if DRY_RUN else created_count} "
        f"skipped={skipped_count} errors={error_count}"
    )
    return 1 if error_count else 0

def main():
    args = parse_args()
    if not 0 <= FUZZ_THRESHOLD <= 100:
        print("[ERR] FUZZ_THRESHOLD must be between 0 and 100.")
        return 2
    if not os.path.isdir(IMG_DIR):
        print(f"[ERR] IMG_DIR not found: {IMG_DIR}")
        return 2

    images = sorted(
        f for f in os.listdir(IMG_DIR)
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        and os.path.isfile(os.path.join(IMG_DIR, f))
    )
    if not images:
        print("[ERR] No images found in IMG_DIR.")
        return 2

    print("[*] Loading performers from Stash...")
    try:
        pairs = load_all_performers(per=200)
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[ERR] Could not load performers: {exc}")
        return 1
    if not pairs and not args.create_missing:
        print("[ERR] No performers returned by Stash.")
        return 1

    corpus = [(normalize_for_match(name), (name, pid)) for (name, pid) in pairs]
    keys   = [c[0] for c in corpus]
    unique_ids = len({pid for _, pid in pairs})
    print(f"[*] Loaded {unique_ids} performers, {len(corpus)} names for matching.")

    if args.create_missing:
        return run_create_missing(images, pairs)

    planned = []
    for fname in images:
        image_path = os.path.join(IMG_DIR, fname)
        base_no_ext = os.path.splitext(fname)[0]
        base_norm = normalize_basename(base_no_ext)

        # Candidate variants from the filename
        variants = {
            base_norm,
            base_norm.replace("_", " "),
            base_norm.replace(".", " "),
            re.sub(r"[._]+", " ", base_norm),
        }
        variants |= {re.sub(r"\b\d{6,}.*$", "", v).strip() for v in list(variants)}
        candidates = {normalize_for_match(v) for v in variants if v}

        best, reason = find_best_match(candidates, keys)
        if not best:
            print(f"[SKIP] {fname}  (base='{base_norm}') :: {reason}")
            continue

        score, idx, cand = best
        orig_name, performer_id = corpus[idx][1]
        planned.append((fname, image_path, score, cand, orig_name, performer_id))

    target_counts = Counter(item[5] for item in planned)
    for fname, image_path, score, cand, orig_name, performer_id in planned:
        if target_counts[performer_id] > 1:
            conflicts = ", ".join(
                item[0] for item in planned if item[5] == performer_id
            )
            print(
                f"[SKIP] {fname} → '{orig_name}' ({performer_id}) :: "
                f"multiple images target this performer: {conflicts}"
            )
            continue

        if DRY_RUN:
            print(f"[DRY] {fname} → '{orig_name}' ({performer_id})  score={score} via '{cand}'")
            continue

        try:
            resp = upload_avatar_auto(performer_id, image_path)
        except (OSError, requests.RequestException) as exc:
            print(f"[ERR] {fname} → '{orig_name}' ({performer_id})  score={score} :: {exc}")
            continue
        try:
            js = resp.json()
        except Exception:
            js = {"status": resp.status_code, "text": resp.text[:200]}

        if resp.ok and "errors" not in js:
            mode = "Upload" if upload_mode == "upload" else "String"
            print(f"[OK]  {fname} → '{orig_name}' ({performer_id})  score={score} via '{cand}'  mode={mode}")
        else:
            print(f"[ERR] {fname} → '{orig_name}' ({performer_id})  score={score} :: {json.dumps(js, ensure_ascii=False)[:500]}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
