# Renaming the project folder

One-off operational note. The project is now called **veille-by-jev**, but the local folder is
still called `Veille Audio` (with a space). It was not renamed straight away because it was the
root of a running development session: renaming the root while commands are running inside it
breaks the shell, the IDE and the file watcher.

Do this **later**, outside any session working inside that folder.

## Why this is not a plain `mv`

The virtual environment hard-codes the absolute path of the project in at least two places:

```text
.venv/lib/python3.11/site-packages/_editable_impl_veille_by_jev.pth
  → /home/toorop/Projects/ai/Veille Audio

.venv/bin/vbj
  → '''exec' '/home/toorop/Projects/ai/Veille Audio/.venv/bin/python3' "$0" "$@"
```

After a `mv`, those paths point nowhere: `uv run vbj` fails with an import error or "Failed to
spawn". Rebuilding `.venv` is not an optional precaution, it is a step of the rename.

Nothing else depends on the folder name: `data/` and `.venv/` are not versioned, so the `mv`
carries them along, and the git history follows the folder.

## The script

Run it **from `~/Projects/ai`**, or as is: it moves itself there. It refuses to run when the
folder is missing, when the target already exists, or when the git tree is dirty.

```bash
#!/usr/bin/env bash
# Rename the project folder to "veille-by-jev", then rebuild the virtual environment,
# whose absolute paths point at the old name.
set -euo pipefail

cd ~/Projects/ai

# 1. Guards: nothing is touched until all three conditions hold.
[ -d "Veille Audio" ] || { echo "Failed: folder 'Veille Audio' not found in $(pwd)"; exit 1; }
[ -d "veille-by-jev" ] && { echo "Failed: 'veille-by-jev' already exists"; exit 1; }
(cd "Veille Audio" && [ -z "$(git status --porcelain)" ]) || {
  echo "Failed: dirty git tree. Commit or stash first."; exit 1;
}

# 2. Rename.
mv "Veille Audio" veille-by-jev
cd veille-by-jev

# 3. Rebuild: .venv and .uv-cache hold stale absolute paths.
rm -rf .venv .uv-cache
uv sync

# 4. Check: the collection already done must be recognised, with no network refetch.
uv run vbj collect --date 2026-09-16
```

Expected output at step 4: the message "`data/2026-09-16/items.json` already exists — nothing
to do", with 984 items and a cost of USD 0.00. If the command starts a network collection
instead, `data/` did not follow the `mv`: stop and check before going further.

## Next: the remote repository

The GitHub repository is public. Before the first `push`, check that no secret and no collected
data leaves with the code (`.env` is ignored, and so are `data/`, `digest/` and `seen.jsonl`).

```bash
cd ~/Projects/ai/veille-by-jev
git remote add origin git@github.com:<account>/veille-by-jev.git
git push -u origin main
```

## If this note is forgotten

Symptom: `uv run vbj …` answers "Failed to spawn: `vbj`" or an import error on `veille`, while
the code is intact.

Fix, inside the renamed folder:

```bash
rm -rf .venv .uv-cache && uv sync
```
