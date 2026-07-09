#!/usr/bin/env python3
# Builds a MiSTer Downloader custom database from the newest matching GitHub
# release asset, and publishes it to an orphan branch of the current repository.
#
# Spec: https://github.com/MiSTer-devel/Downloader_MiSTer/blob/main/docs/custom-databases.md
# Conventions follow https://github.com/theypsilon/DB-Template_MiSTer
#
# Configuration is taken from environment variables (see action.yml):
#   GITHUB_REPOSITORY     repository that hosts the database (owner/name)
#   GITHUB_SHA            commit used to pin raw URLs of EXTRA_FILES
#   GITHUB_TOKEN          token for GitHub API calls and pushing the db branch
#   SOURCE_REPO           repository whose releases hold the assets (defaults to GITHUB_REPOSITORY)
#   RELEASE_TAG           build from this tag (defaults to the latest release)
#   ASSET_PATTERN         glob selecting the release asset, e.g. "MegaDrive_Paprium_*.rbf"
#   ASSET_INSTALL_DIR     install dir relative to /media/fat, e.g. "_Custom Cores/Cores"
#   ASSET_TANGLE          optional entanglement tag for the asset
#   EXTRA_FILES           newline-separated "repo/path => install/path" entries
#   REBOOT_PATHS          newline-separated install paths flagged with reboot
#   PEXT_FOLDERS          newline-separated folders created as potentially-external
#   DB_ID                 database id (defaults to lowercased GITHUB_REPOSITORY)
#   DB_BRANCH             orphan branch to publish to (defaults to "db")
#   RUN_DOWNLOADER_TEST   validate the database with a real downloader run (defaults to "true")
#   DRY_RUN               build and test, but do not push (defaults to "false")

import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

USER_AGENT = 'mister-release-db'


def main() -> int:
    repo = require_env('GITHUB_REPOSITORY')
    source_repo = os.getenv('SOURCE_REPO', '').strip() or repo
    asset_pattern = require_env('ASSET_PATTERN')
    asset_install_dir = require_env('ASSET_INSTALL_DIR').strip('/')
    asset_tangle = os.getenv('ASSET_TANGLE', '').strip()
    release_tag = os.getenv('RELEASE_TAG', '').strip()
    db_id = (os.getenv('DB_ID', '').strip() or repo).lower()
    db_branch = os.getenv('DB_BRANCH', '').strip() or 'db'
    dry_run = os.getenv('DRY_RUN', 'false').strip().lower() == 'true'
    run_test = os.getenv('RUN_DOWNLOADER_TEST', 'true').strip().lower() != 'false'
    extra_files = parse_extra_files(os.getenv('EXTRA_FILES', ''))
    reboot_paths = set(parse_lines(os.getenv('REBOOT_PATHS', '')))
    pext_folders = parse_lines(os.getenv('PEXT_FOLDERS', ''))

    release = fetch_release(source_repo, release_tag)
    if release is None:
        log(f'Repository {source_repo} has no published releases yet. Nothing to build.')
        return 0

    asset = pick_asset(release, asset_pattern)
    log(f"Building database '{db_id}' from {source_repo} release '{release['tag_name']}' asset '{asset['name']}'")

    files = {}

    asset_path = f"{asset_install_dir}/{asset['name']}"
    asset_bytes = http_get(asset['browser_download_url'])
    files[asset_path] = file_description(asset_bytes, asset['browser_download_url'], reboot_paths, asset_path)
    if asset_tangle != '':
        files[asset_path]['tangle'] = [asset_tangle]

    for source, target in extra_files:
        files[target] = file_description(read_repo_file(source), pinned_raw_url(repo, source), reboot_paths, target)

    db = {
        'db_id': db_id,
        'v': 1,
        'timestamp': int(time.time()),
        'files': dict(sorted(files.items())),
        'folders': build_folders(files, pext_folders),
    }

    with open('db.json', 'w', encoding='utf-8', newline='\n') as f:
        json.dump(db, f, indent=4, sort_keys=True)
    with zipfile.ZipFile('db.json.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write('db.json')

    log('Database contents:')
    log(json.dumps(db, indent=4, sort_keys=True))

    db_url = f'https://raw.githubusercontent.com/{repo}/{db_branch}/db.json.zip'
    drop_in_files = create_drop_in_database_files(db_id, db_url)

    if run_test:
        test_database(db_id)

    if dry_run:
        log('Dry run: skipping push.')
        return 0

    push_database(db_branch, drop_in_files, f"Database for {source_repo} {release['tag_name']} ({asset['name']})")
    log(f'Done. Database published at: {db_url}')
    return 0


## RELEASE SELECTION

def fetch_release(source_repo, release_tag):
    if release_tag != '':
        return github_api(f'/repos/{source_repo}/releases/tags/{urllib.parse.quote(release_tag)}')

    try:
        return github_api(f'/repos/{source_repo}/releases/latest')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    # No stable release: fall back to the newest non-draft release (e.g. prereleases only)
    releases = [r for r in github_api(f'/repos/{source_repo}/releases') if not r.get('draft', False)]
    return releases[0] if releases else None


def pick_asset(release, asset_pattern):
    matching = [a for a in release.get('assets', []) if fnmatch.fnmatch(a['name'], asset_pattern)]
    if len(matching) == 0:
        available = ', '.join(a['name'] for a in release.get('assets', [])) or '<none>'
        raise SystemExit(f"No asset matching '{asset_pattern}' in release '{release['tag_name']}'. Assets: {available}")

    matching.sort(key=lambda a: a['name'])
    if len(matching) > 1:
        log(f"Multiple assets match '{asset_pattern}': {', '.join(a['name'] for a in matching)}. Using the newest one.")
    return matching[-1]


## DATABASE CONSTRUCTION

def file_description(content, url, reboot_paths, target_path):
    description = {
        'hash': hashlib.md5(content).hexdigest(),
        'size': len(content),
        'url': url,
    }
    if target_path in reboot_paths:
        description['reboot'] = True
    return description


def build_folders(files, pext_folders):
    folders = {}
    for file_path in files:
        add_with_parents(folders, posixpath.dirname(file_path))
    for folder in pext_folders:
        add_with_parents(folders, folder.strip('/'))
        folders[folder.strip('/')] = {'path': 'pext'}
    return dict(sorted(folders.items()))


def add_with_parents(folders, folder):
    while folder not in ('', '.', '/'):
        folders.setdefault(folder, {})
        folder = posixpath.dirname(folder)


def read_repo_file(source):
    if not os.path.isfile(source):
        raise SystemExit(f"EXTRA_FILES entry '{source}' does not exist in the repository checkout.")
    with open(source, 'rb') as f:
        return f.read()


def pinned_raw_url(repo, source):
    commit = os.getenv('GITHUB_SHA', '').strip()
    if commit == '':
        commit = run_stdout(['git', 'rev-parse', 'HEAD'])
    return f'https://raw.githubusercontent.com/{repo}/{commit}/{urllib.parse.quote(source)}'


def create_drop_in_database_files(db_id, db_url):
    sanitized_db_id = re.sub(r'[^A-Za-z0-9._-]+', '_', db_id).strip('._-')
    if sanitized_db_id == '':
        raise SystemExit(f'Unable to derive a drop-in filename from db_id "{db_id}"')

    drop_in_ini = f'downloader_{sanitized_db_id}.ini'
    drop_in_zip = f'downloader_{sanitized_db_id}.zip'
    drop_in_contents = f'[{db_id}]\ndb_url = {db_url}\n'

    with open(drop_in_ini, 'w', encoding='utf-8', newline='\n') as f:
        f.write(drop_in_contents)

    with zipfile.ZipFile(drop_in_zip, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(drop_in_ini, drop_in_contents)

    return [drop_in_ini, drop_in_zip]


## VALIDATION

def test_database(db_id):
    log('\nTesting database with a real downloader run...\n')
    with tempfile.TemporaryDirectory() as temp_folder:
        test_script = temp_folder + '/downloader_test.py'
        with open(test_script, 'wb') as f:
            f.write(http_get('https://github.com/MiSTer-devel/Downloader_MiSTer/releases/download/latest/downloader_test.py'))
        run([sys.executable, test_script, db_id, os.path.abspath('db.json')])
    log('\nThe test went well.\n')


## PUBLISHING

def push_database(db_branch, drop_in_files, message):
    log(f"Pushing database to branch '{db_branch}'...")
    run(['git', 'config', 'user.email', 'github-actions[bot]@users.noreply.github.com'])
    run(['git', 'config', 'user.name', 'github-actions[bot]'])
    run(['git', 'checkout', '--orphan', db_branch])
    run(['git', 'reset'])
    run(['git', 'add', '-f', 'db.json.zip', *drop_in_files])
    run(['git', 'commit', '-m', message])
    run(['git', 'push', '--force', 'origin', db_branch])


## HELPERS

def github_api(path):
    request = urllib.request.Request(f'https://api.github.com{path}', headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/vnd.github+json',
    })
    token = os.getenv('GITHUB_TOKEN', '').strip()
    if token != '':
        request.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def http_get(url):
    # No Authorization header: release asset downloads redirect to signed URLs
    # that reject requests carrying extra credentials.
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def parse_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip() != '']


def parse_extra_files(text):
    entries = []
    for line in parse_lines(text):
        parts = [part.strip().strip('/') for part in line.split('=>')]
        if len(parts) != 2 or '' in parts:
            raise SystemExit(f"Invalid EXTRA_FILES entry '{line}'. Expected format: repo/path => install/path")
        entries.append((parts[0], parts[1]))
    return entries


def require_env(name):
    value = os.getenv(name, '').strip()
    if value == '':
        raise SystemExit(f'Missing required environment variable: {name}')
    return value


def run(commands):
    log('> ' + ' '.join(commands))
    subprocess.run(commands, check=True, stderr=subprocess.STDOUT)


def run_stdout(commands):
    return subprocess.run(commands, check=True, capture_output=True, text=True).stdout.strip()


def log(*text):
    print(*text, flush=True)


if __name__ == '__main__':
    sys.exit(main())
