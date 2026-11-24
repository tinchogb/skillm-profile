import argparse
import re
import subprocess
import sys
from pathlib import Path

from skillm_profile.config import (
    AppConfig,
    ConfigError,
    default_xdg_config_path,
    load_config,
    resolve_config_path,
)
from skillm_profile.core import (
    SkillmProfileError,
    add_skills,
    bootstrap_workspace,
    create_profile,
    delete_profile,
    init_workspace,
    list_profiles,
    remove_skills,
    sync_all,
    sync_profile,
)
from skillm_profile.manifest import ManifestError
from skillm_profile.paths import ProfilePaths
from skillm_profile.runner import SkillmCommandError


def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{question}{suffix}: ").strip()
    return raw if raw else default


def _detect_github_remote(repo_dir: Path) -> str:
    if not (repo_dir / ".git").exists():
        return ""
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    url = proc.stdout.strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    return match.group(1) if match else ""


def _run_init(args: argparse.Namespace) -> int:
    config_path_preview = resolve_config_path(args.config)
    existing_cfg = None
    if config_path_preview.exists():
        try:
            existing_cfg = load_config(config_path_preview)
        except ConfigError:
            existing_cfg = None

    default_collection = str(
        existing_cfg.collection_path if existing_cfg else Path.home() / "skills-collection"
    )
    default_cfg = str(config_path_preview)

    if args.yes:
        collection_raw = args.collection_dir or default_collection
        collection_path = Path(collection_raw).expanduser().resolve()
        remote_raw = (
            args.remote if args.remote is not None else _detect_github_remote(collection_path)
        )
        config_raw = args.config or default_cfg
    else:
        collection_raw = args.collection_dir or _prompt(
            "Path to local skills-collection repository", default_collection
        )
        collection_path = Path(collection_raw).expanduser().resolve()
        detected_remote = _detect_github_remote(collection_path)
        remote_raw = (
            args.remote
            if args.remote is not None
            else _prompt("GitHub repository (owner/repo, optional)", detected_remote)
        )
        config_raw = args.config or _prompt("Configuration file path", default_cfg)

    config_path = Path(config_raw).expanduser().resolve()
    xdg_default = default_xdg_config_path().resolve()

    config = AppConfig(
        collection_path=collection_path,
        collection_remote=remote_raw.strip(),
    )

    actions = init_workspace(config, config_path, force=args.force)
    for line in actions:
        print(line)

    if config_path != xdg_default:
        print("\nCustom config path selected. Add this to your ~/.bashrc:")
        print(f'  export SKILLM_PROFILE_CONFIG="{config_path}"')

    return 0


def _add_config_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Path to skillm-profile.toml (overrides SKILLM_PROFILE_CONFIG and XDG default).",
    )


def build_parser() -> argparse.ArgumentParser:
    epilog = """\
configuration precedence:
  1. --config PATH
  2. $SKILLM_PROFILE_CONFIG
  3. ${XDG_CONFIG_HOME:-~/.config}/skillm-profile/skillm-profile.toml

examples:
  skillm-profile init
  skillm-profile bootstrap
  skillm-profile create research
  skillm-profile add research my-skill-a my-skill-b
  skillm-profile sync --all
"""

    parser = argparse.ArgumentParser(
        prog="skillm-profile",
        description="Manage isolated skillm profiles backed by a centralized skills repository.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_config_flag(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init",
        help="Initialize skillm-profile configuration and workspace.",
    )
    _add_config_flag(p_init)
    p_init.add_argument(
        "--collection-dir",
        metavar="DIR",
        help="Local path to skills-collection repository (default: ~/skills-collection).",
    )
    p_init.add_argument(
        "--remote",
        metavar="OWNER/REPO",
        help="GitHub repository shorthand (auto-detected from git origin if available).",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration file.",
    )
    p_init.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Accept defaults without interactive prompts.",
    )

    p_boot = sub.add_parser(
        "bootstrap",
        help="Install global skills from the configured collection and sync all profiles.",
    )
    _add_config_flag(p_boot)
    p_boot.add_argument(
        "--source",
        metavar="SOURCE",
        help="Override collection source (GitHub owner/repo, URL, or local path).",
    )
    p_boot.add_argument(
        "--force",
        action="store_true",
        help="Pass --force to 'skillm install' when taking over existing agent copies.",
    )

    p_list = sub.add_parser("list", help="List configured profiles and their skills.")
    _add_config_flag(p_list)

    p_create = sub.add_parser("create", help="Create a new empty profile.")
    _add_config_flag(p_create)
    p_create.add_argument("profile", help="Profile name.")

    p_delete = sub.add_parser("delete", help="Delete a profile from profiles.toml.")
    _add_config_flag(p_delete)
    p_delete.add_argument("profile", help="Profile name.")
    p_delete.add_argument(
        "--purge",
        action="store_true",
        help="Also remove the materialized profile directory.",
    )

    p_add = sub.add_parser("add", help="Add globally installed skills to a profile and sync.")
    _add_config_flag(p_add)
    p_add.add_argument("profile", help="Profile name.")
    p_add.add_argument("skills", nargs="+", metavar="SKILL", help="Skill IDs to add.")

    p_remove = sub.add_parser("remove", help="Remove skills from a profile and sync.")
    _add_config_flag(p_remove)
    p_remove.add_argument("profile", help="Profile name.")
    p_remove.add_argument("skills", nargs="+", metavar="SKILL", help="Skill IDs to remove.")

    p_sync = sub.add_parser(
        "sync",
        help="Reconcile materialized profile directories with profiles.toml.",
    )
    _add_config_flag(p_sync)
    sync_group = p_sync.add_mutually_exclusive_group(required=True)
    sync_group.add_argument("profile", nargs="?", help="Profile name to sync.")
    sync_group.add_argument("--all", action="store_true", help="Sync all defined profiles.")

    parser.set_defaults(config=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return _run_init(args)

        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
        paths = ProfilePaths.from_config(config)

        if args.command == "bootstrap":
            result = bootstrap_workspace(config, paths, source=args.source, force=args.force)
            print(f"Bootstrapped from '{result['source']}'.")
            for name, summary in result["profiles"].items():
                print(f"Synced '{name}': {summary}")
            return 0

        if args.command == "list":
            profiles = list_profiles(paths)
            if not profiles:
                print("No profiles defined.")
                return 0
            for name, skills in profiles.items():
                joined = ", ".join(skills) if skills else "(empty)"
                print(f"{name}: {joined}")
            return 0

        if args.command == "create":
            create_profile(paths, args.profile)
            print(f"Created profile '{args.profile}'.")
            return 0

        if args.command == "delete":
            delete_profile(paths, args.profile, purge=args.purge)
            print(f"Deleted profile '{args.profile}'.")
            return 0

        if args.command == "add":
            added = add_skills(paths, args.profile, args.skills)
            print(f"Profile '{args.profile}' synced (added: {', '.join(added) or 'none'}).")
            return 0

        if args.command == "remove":
            removed = remove_skills(paths, args.profile, args.skills)
            print(f"Profile '{args.profile}' synced (removed: {', '.join(removed) or 'none'}).")
            return 0

        if args.command == "sync":
            if args.all:
                results = sync_all(paths)
                for name, summary in results.items():
                    print(f"Synced '{name}': {summary}")
                return 0
            summary = sync_profile(paths, args.profile)
            print(f"Synced '{args.profile}': {summary}")
            return 0

    except (ConfigError, ManifestError, SkillmProfileError, SkillmCommandError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0
