import argparse
import os
import shutil
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


def _prompt(question: str, default: str | None = None, hint: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else f" ({hint})"
    raw = input(f"{question}{suffix}: ").strip()
    return raw if raw else (default or "")


def _validate_existing_dir(raw: str, label: str) -> Path:
    if not raw.strip():
        raise SkillmProfileError(f"{label} is required and cannot be empty.")
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise SkillmProfileError(f"{label} does not exist or is not a directory: '{path}'.")
    return path


def _validate_creatable_dir(raw: str, label: str) -> Path:
    if not raw.strip():
        raise SkillmProfileError(f"{label} is required and cannot be empty.")
    path = Path(raw).expanduser().resolve()
    if path.exists():
        if not path.is_dir():
            raise SkillmProfileError(f"{label} exists and is not a directory: '{path}'.")
        if not os.access(path, os.W_OK):
            raise SkillmProfileError(f"{label} is not writable: '{path}'.")
        return path
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists() or not os.access(probe, os.W_OK):
        raise SkillmProfileError(
            f"Cannot create {label} at '{path}': parent '{probe}' is not writable."
        )
    return path


def _validate_target_file(raw: str, label: str, force: bool) -> Path:
    if not raw.strip():
        raise SkillmProfileError(f"{label} is required and cannot be empty.")
    path = Path(raw).expanduser().resolve()
    if path.exists() and not force:
        raise SkillmProfileError(f"{label} already exists at '{path}'. Use --force to overwrite.")
    if path.exists() and path.is_dir():
        raise SkillmProfileError(f"{label} path '{path}' is a directory, not a file.")
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists() or not os.access(probe, os.W_OK):
        raise SkillmProfileError(
            f"Cannot write {label} at '{path}': parent '{probe}' is not writable."
        )
    return path


def _run_init(args: argparse.Namespace) -> int:
    if shutil.which("skillm") is None:
        raise SkillmProfileError(
            "The 'skillm' binary was not found in PATH. Install skillm before running init."
        )

    default_profiles_dir = str(Path.home() / ".skillm" / "profiles")
    default_cfg = str(resolve_config_path(args.config))

    if args.yes:
        if not args.collection_dir:
            raise SkillmProfileError(
                "--collection-dir is required when using --yes in non-interactive mode."
            )
        collection_path = _validate_existing_dir(args.collection_dir, "Collection directory")
        remote_raw = args.remote.strip() if args.remote else ""
        profiles_dir = _validate_creatable_dir(
            args.profiles_dir or default_profiles_dir, "Profiles directory"
        )
        config_path = _validate_target_file(
            args.config or default_cfg, "Configuration file", force=args.force
        )
    else:
        collection_raw = args.collection_dir or _prompt(
            "Path to existing skills-collection repository",
            hint="required, existing directory",
        )
        collection_path = _validate_existing_dir(collection_raw, "Collection directory")

        remote_raw = (
            args.remote.strip()
            if args.remote is not None
            else _prompt(
                "GitHub repository owner/repo",
                hint="optional, press Enter to skip",
            )
        )

        profiles_raw = args.profiles_dir or _prompt(
            "Directory where skillm profiles will be materialized",
            default=default_profiles_dir,
        )
        profiles_dir = _validate_creatable_dir(profiles_raw, "Profiles directory")

        config_raw = args.config or _prompt("Configuration file path", default=default_cfg)
        config_path = _validate_target_file(config_raw, "Configuration file", force=args.force)

    xdg_default = default_xdg_config_path().resolve()
    config = AppConfig(
        collection_path=collection_path,
        collection_remote=remote_raw,
        profiles_dir=profiles_dir,
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
        help="Path to an existing skills-collection repository (required with --yes).",
    )
    p_init.add_argument(
        "--remote",
        metavar="OWNER/REPO",
        help="Optional GitHub repository shorthand (e.g. owner/repo).",
    )
    p_init.add_argument(
        "--profiles-dir",
        metavar="DIR",
        help="Directory where profile environments are materialized (default: ~/.skillm/profiles).",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration file or keep existing profiles.toml.",
    )
    p_init.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive mode (requires --collection-dir).",
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
