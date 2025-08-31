# Stash Performer Image Loader (Fuzzy Match)

Assign local images to **custom performers** in Stash by fuzzy-matching filenames to performer names.
Works even if your Stash schema expects `image: Upload` **or** `image: String` — the script auto-detects and falls back.

## Requirements
- Python 3.9+
- `requests`, `rapidfuzz`

Install:
```bash
python3 -m pip install -r requirements.txt
```

## Setup
1. Copy the env template and fill your values:
   ```bash
   cp .env.example .env
   # edit .env with your STASH_URL, API_KEY, IMG_DIR, etc.
   ```
2. Put your images in `IMG_DIR` (or point `IMG_DIR` to your folder).

## Run
```bash
python3 scripts/find_custom_performers_pfp.py
```

Optional flags:
- Set `DRY_RUN=true` in `.env` to preview without uploading
- Tweak `FUZZ_THRESHOLD` (70–80 is a good range for messy data)

## License
MIT – see LICENSE.
