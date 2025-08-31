#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assign local images to Stash custom performers using fuzzy name matching.
Auto-detects whether your Stash expects image Upload or String and falls back.

Usage:
  - Put config in .env (see .env.example)
  - python3 scripts/find_custom_performers_pfp.py
"""
import os, re, json, base64, mimetypes, requests
from rapidfuzz import process, fuzz

def getenv_bool(k: str, default: bool) -> bool:
    v = os.getenv(k)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")

STASH_URL      = os.getenv("STASH_URL", "http://localhost:9999/graphql")
API_KEY        = os.getenv("API_KEY", "")
IMG_DIR        = os.getenv("IMG_DIR", ".")
FUZZ_THRESHOLD = int(os.getenv("FUZZ_THRESHOLD", "75"))
DRY_RUN        = getenv_bool("DRY_RUN", False)

HEADERS = {"ApiKey": API_KEY}

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

# ---- upload mode (auto) ----
class UploadMode:
    AUTO = "auto"
    UPLOAD = "upload"
    STRING = "string"

upload_mode = UploadMode.AUTO

def send_upload(performer_id: str, image_path: str):
    ops = {"query": UPDATE_PERFORMER.replace("\n", " "),
           "variables": {"id": performer_id, "image": None}}
    files = {
        "operations": (None, json.dumps(ops), "application/json"),
        "map": (None, '{"0": ["variables.image"]}'),
        "0": (os.path.basename(image_path), open(image_path, "rb"), mime_for(image_path)),
    }
    return requests.post(STASH_URL, files=files, headers=HEADERS, timeout=120)

def send_string(performer_id: str, image_path: str):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    data_uri = f"data:{mime_for(image_path)};base64,{b64}"
    payload = {"query": UPDATE_PERFORMER_STRING, "variables": {"id": performer_id, "image": data_uri}}
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

    err_text = json.dumps(js, ensure_ascii=False)
    if "expecting type \\\"String\\\"" in err_text or "expecting type \"String\"" in err_text:
        upload_mode = UploadMode.STRING
        return send_string(performer_id, image_path)

    return resp

def main():
    if not API_KEY or not isinstance(API_KEY, str):
        print("[ERR] API_KEY is empty or not a string. Put it in quotes."); return
    if not os.path.isdir(IMG_DIR):
        print(f"[ERR] IMG_DIR not found: {IMG_DIR}"); return

    images = [f for f in os.listdir(IMG_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    if not images:
        print("[ERR] No images found in IMG_DIR."); return

    print("[*] Loading performers from Stash...]")
    pairs = load_all_performers(per=200)
    if not pairs:
        print("[ERR] No performers returned by Stash."); return

    corpus = [(normalize_for_match(name), (name, pid)) for (name, pid) in pairs]
    keys   = [c[0] for c in corpus]
    unique_ids = len({pid for _, pid in pairs})
    print(f"[*] Loaded {unique_ids} performers, {len(corpus)} names for matching.")

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

        best = None
        for cand in candidates:
            hit = process.extractOne(cand, keys, scorer=fuzz.token_set_ratio)
            if hit:
                matched_value, score, idx = hit  # (value, score, index)
                if (best is None) or (score > best[0]):
                    best = (score, idx, cand)

        if not best or best[0] < FUZZ_THRESHOLD:
            print(f"[SKIP] No fuzzy match ≥{FUZZ_THRESHOLD} for: {fname}  (base='{base_norm}')  best={best[0] if best else 'n/a'}")
            continue

        score, idx, cand = best
        orig_name, performer_id = corpus[idx][1]

        if DRY_RUN:
            print(f"[DRY] {fname} → '{orig_name}' ({performer_id})  score={score} via '{cand}'")
            continue

        resp = upload_avatar_auto(performer_id, image_path)
        try:
            js = resp.json()
        except Exception:
            js = {"status": resp.status_code, "text": resp.text[:200]}

        if resp.ok and "errors" not in js:
            mode = "Upload" if upload_mode == "upload" else "String"
            print(f"[OK]  {fname} → '{orig_name}' ({performer_id})  score={score} via '{cand}'  mode={mode}")
        else:
            print(f"[ERR] {fname} → '{orig_name}' ({performer_id})  score={score} :: {json.dumps(js, ensure_ascii=False)[:500]}")

if __name__ == "__main__":
    main()
