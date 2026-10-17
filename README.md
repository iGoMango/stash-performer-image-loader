# StashApp Performer Image Loader

Assign local images to existing StashApp performers, or create performers that
do not already exist. The script previews changes by default.

## Requirements

- Python 3.9+
- A running StashApp instance

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your StashApp URL, API key, and image directory:

```dotenv
STASH_URL="http://localhost:9999/graphql"
API_KEY="PUT_YOUR_API_KEY_HERE"
IMG_DIR="/absolute/path/to/your/images"
FUZZ_THRESHOLD=75
DRY_RUN=true
```

Supported image types are JPEG, PNG, and WebP. The `.env` file and `images/`
directory are ignored by Git.

## Match existing performers and set their photos

```bash
.venv/bin/python scripts/find_custom_performers_pfp.py
```

The script compares image filenames with existing performer names. It skips:

- short partial names that could match the wrong performer;
- matches whose top scores are too close to distinguish safely;
- multiple images that target the same performer; and
- matches below `FUZZ_THRESHOLD`.

StashApp image-field differences are handled automatically (`Upload` or
`String`).

## Create missing performers

```bash
.venv/bin/python scripts/find_custom_performers_pfp.py --create-missing
```

This mode does not fuzzy-match or modify existing performers. For each image:

- the filename without its extension becomes the performer name;
- an exact normalized name check is made against StashApp;
- an existing performer is reported as `EXISTS` and left unchanged; and
- a missing performer is created with the image as its profile photo.

Name checks are case-insensitive and treat spaces, periods, and underscores as
equivalent. For example, `Jane_Doe.jpg` is considered the same name as an
existing `Jane Doe` performer.

## Safety workflow

`DRY_RUN=true` is the default. In dry-run mode the script reads StashApp data
and prints planned actions but does not upload photos or create performers.

1. Run the intended command with `DRY_RUN=true`.
2. Review every `DRY` or `DRY-CREATE` line, especially unusual filenames.
3. Change `DRY_RUN=false` in `.env` only when the preview is correct.
4. Run the same command again to apply the changes.
5. Restore `DRY_RUN=true` afterward.

## License

MIT — see [LICENSE](LICENSE).
