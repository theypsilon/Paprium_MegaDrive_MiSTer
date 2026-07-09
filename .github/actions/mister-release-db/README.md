# MiSTer Release Database action

Builds a [MiSTer Downloader custom database](https://github.com/MiSTer-devel/Downloader_MiSTer/blob/main/docs/custom-databases.md)
from the newest matching GitHub release asset and publishes it to an orphan branch
(`db` by default), following the same conventions as
[DB-Template_MiSTer](https://github.com/theypsilon/DB-Template_MiSTer).

The published branch contains:

- `db.json.zip` — the database, served at `https://raw.githubusercontent.com/<owner>/<repo>/<db-branch>/db.json.zip`
- `downloader_<db_id>.ini` / `.zip` — [drop-in database files](https://github.com/MiSTer-devel/Downloader_MiSTer/blob/main/docs/drop-in-databases.md)
  users can copy next to `downloader.ini`

How it works:

1. Picks the newest release of `source-repo` (or the given `release-tag`) and selects
   the asset matching `asset-pattern`. The asset is installed at
   `<asset-install-dir>/<asset name>` and downloaded by users directly from the
   GitHub release URL.
2. `extra-files` (e.g. an `.mgl` launcher) are read from the repository checkout and
   served via raw URLs pinned to the current commit SHA, so their hashes never drift.
3. Folders are derived from the file paths; `pext-folders` are additionally created
   as potentially-external paths, and `reboot-paths` are flagged to request a reboot.
4. Before publishing, the database is validated with a real run of the actual
   downloader (`run-downloader-test`).

See `action.yml` for all inputs. The action is self-contained in this directory
(`action.yml` + `build_release_db.py`), so it can be extracted to its own repository
and referenced as `uses: <owner>/<repo>@<ref>` without changes — only the calling
workflow's `uses:` line needs updating.

Local dry run:

```bash
GITHUB_REPOSITORY=owner/repo \
ASSET_PATTERN='MegaDrive_Paprium_*.rbf' \
ASSET_INSTALL_DIR='_Custom Cores/Cores' \
DRY_RUN=true \
python3 build_release_db.py
```
