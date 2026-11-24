# skillm-profile

Manage isolated [skillm](https://github.com) profile environments backed by a centralized, version-controlled skills repository.

## Overview

`skillm-profile` adds named profile workspaces on top of `skillm`. Instead of duplicating skill files, managing per-folder lockfiles manually, or storing user data inside `~/.skillm/`, you declare which skills belong to each profile in a single `profiles.toml` manifest inside your skills repository, and `skillm-profile` reconciles each profile directory using `skillm` under the hood.

### Key Features

- **Centralized & Version-Controlled**: Reads and updates `profiles.toml` directly inside your local skills repository (e.g. `skills-collection`).
- **XDG-Compliant Configuration & State**: Stores configuration in `${XDG_CONFIG_HOME:-~/.config}/skillm-profile/skillm-profile.toml` (overridable via `SKILLM_PROFILE_CONFIG` or `--config`) and materializes profile workspaces outside `~/.skillm/` (e.g. `~/.local/share/skillm-profile/profiles`).
- **One-Command Machine Bootstrap**: Reproduces your global `skillm` catalog and all materialized profile environments on a fresh machine via `skillm-profile bootstrap`.
- **Untracked Skill Ingestion**: Automatically detects manually created skills inside a profile directory during `sync`, moves them into your repository's `custom/` folder, registers them globally in `skillm`, and records them in `profiles.toml`.

## Prerequisites

- Python `>= 3.11`
- [`skillm`](https://github.com) installed and available in your `PATH` (tested with `skillm v0.7.1`, `list --json` schema `v1`)
- A local directory (typically a Git repository) holding your skills and `profiles.toml`

## Installation

Install globally using [`uv`](https://astral.sh):

```bash
uv tool install .
```

Or install directly from GitHub:

```bash
uv tool install git+https://github.com
```

## Quickstart

1. **Initialize configuration and workspace:**

   ```bash
   skillm-profile init
   ```

   Follow the interactive prompts to specify:
   - Path to your existing local `skills-collection` directory
   - Optional GitHub repository (`owner/repo`) for remote bootstrapping
   - Directory where profile environments will be materialized (e.g. `~/.local/share/skillm-profile/profiles`)
   - Location of `skillm-profile.toml` (defaults to `~/.config/skillm-profile/skillm-profile.toml`)

2. **Bootstrap an existing collection on a new machine:**

   ```bash
   skillm-profile bootstrap
   ```

3. **Create and manage profiles:**

   ```bash
   skillm-profile create research
   skillm-profile add research zotero-cli zotero-library-curator
   skillm-profile sync --all
   ```

## Configuration

`skillm-profile` resolves its configuration file in the following order:

1. `--config PATH` CLI flag
2. `$SKILLM_PROFILE_CONFIG` environment variable
3. `${XDG_CONFIG_HOME:-~/.config}/skillm-profile/skillm-profile.toml`

### Example `skillm-profile.toml`

```toml
[collection]
path = "/home/user/Projects/skills-collection"
remote = "owner/skills-collection"

[paths]
profiles_manifest = "profiles.toml"
profiles_dir = "/home/user/.local/share/skillm-profile/profiles"
custom_dir = "custom"
third_party_dir = "third-party"
```

### Example `profiles.toml`

Stored inside `[collection].path` and tracked in Git:

```toml
[profiles.research]
skills = [
    "zotero-cli",
    "zotero-library-curator",
    "zotero-semantic-tagger",
]
```

## Commands

| Command | Description |
| :--- | :--- |
| `skillm-profile init [--collection-dir DIR] [--remote OWNER/REPO] [--profiles-dir DIR] [--force] [--yes]` | Validate paths and create `skillm-profile.toml` and `profiles.toml`. |
| `skillm-profile bootstrap [--source SOURCE] [--force]` | Install all global skills from `[collection].remote` (or `path`) via `skillm` and sync all profiles. |
| `skillm-profile list` | Display all profiles defined in `profiles.toml` and their assigned skills. |
| `skillm-profile create <profile>` | Add a new empty profile entry to `profiles.toml`. |
| `skillm-profile delete <profile> [--purge]` | Remove a profile from `profiles.toml` (and delete its materialized directory with `--purge`). |
| `skillm-profile add <profile> <skill...>` | Add globally installed skills to a profile and sync its directory. |
| `skillm-profile remove <profile> <skill...>` | Remove skills from a profile and sync its directory. |
| `skillm-profile sync (<profile> \| --all)` | Ingest untracked local skills and reconcile profile directories with `profiles.toml`. |

## Development

Run formatting, linting, and tests with 100% coverage:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest --cov=skillm_profile --cov-report=term-missing -v
```