import shutil
from pathlib import Path

from skillm_profile.config import AppConfig, save_config
from skillm_profile.manifest import load_manifest, save_manifest
from skillm_profile.paths import ProfilePaths
from skillm_profile.runner import (
    SkillmCommandError,
    get_global_skills,
    get_project_skills,
    run_skillm,
)


class SkillmProfileError(RuntimeError):
    """Domain error for skillm-profile operations."""


def init_workspace(
    config: AppConfig,
    config_path: Path,
    force: bool = False,
) -> list[str]:
    paths = ProfilePaths.from_config(config)
    actions: list[str] = []

    if config_path.exists() and not force:
        raise SkillmProfileError(
            f"Config file already exists at {config_path}. Use --force to overwrite."
        )

    paths.collection_dir.mkdir(parents=True, exist_ok=True)
    paths.custom_dir.mkdir(parents=True, exist_ok=True)
    paths.third_party_dir.mkdir(parents=True, exist_ok=True)
    paths.profiles_dir.mkdir(parents=True, exist_ok=True)

    if not paths.profiles_toml.exists():
        paths.profiles_toml.write_text("[profiles]\n", encoding="utf-8")
        actions.append(f"Created {paths.profiles_toml}")
    else:
        actions.append(f"Kept existing {paths.profiles_toml}")

    save_config(config, config_path)
    actions.append(f"Saved configuration to {config_path}")
    return actions


def list_profiles(paths: ProfilePaths) -> dict[str, list[str]]:
    return load_manifest(paths.profiles_toml)


def create_profile(paths: ProfilePaths, profile: str) -> None:
    manifest = load_manifest(paths.profiles_toml)
    if profile in manifest:
        raise SkillmProfileError(f"Profile '{profile}' already exists.")
    manifest[profile] = []
    save_manifest(paths.profiles_toml, manifest)


def delete_profile(paths: ProfilePaths, profile: str, purge: bool = False) -> None:
    manifest = load_manifest(paths.profiles_toml)
    if profile not in manifest:
        raise SkillmProfileError(f"Profile '{profile}' does not exist.")
    del manifest[profile]
    save_manifest(paths.profiles_toml, manifest)

    target_dir = paths.profile_dir(profile)
    if purge and target_dir.exists():
        shutil.rmtree(target_dir)


def _validate_global_skills(skills: list[str]) -> None:
    if not skills:
        return
    global_skills = get_global_skills()
    missing = [s for s in skills if s not in global_skills]
    if missing:
        raise SkillmProfileError(
            f"Skills not installed globally in skillm: {', '.join(missing)}. "
            "Install them globally first (e.g., via 'skillm install <repo> --global')."
        )


def add_skills(paths: ProfilePaths, profile: str, skills: list[str]) -> list[str]:
    manifest = load_manifest(paths.profiles_toml)
    if profile not in manifest:
        raise SkillmProfileError(f"Profile '{profile}' does not exist. Create it first.")

    _validate_global_skills(skills)

    current = manifest[profile]
    added: list[str] = []
    for skill in skills:
        if skill not in current:
            current.append(skill)
            added.append(skill)

    save_manifest(paths.profiles_toml, manifest)
    sync_profile(paths, profile)
    return added


def remove_skills(paths: ProfilePaths, profile: str, skills: list[str]) -> list[str]:
    manifest = load_manifest(paths.profiles_toml)
    if profile not in manifest:
        raise SkillmProfileError(f"Profile '{profile}' does not exist.")

    current = manifest[profile]
    removed = [s for s in skills if s in current]
    manifest[profile] = [s for s in current if s not in skills]
    save_manifest(paths.profiles_toml, manifest)
    sync_profile(paths, profile)
    return removed


def ingest_untracked_skills(paths: ProfilePaths, profile: str) -> list[str]:
    """Detect manually created skills inside a profile dir and move them to custom_dir."""
    skills_dir = paths.profile_skills_dir(profile)
    if not skills_dir.exists():
        return []

    tracked = set(get_project_skills(paths.profile_dir(profile)))
    ingested: list[str] = []

    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skill_id = entry.name
        if skill_id in tracked:
            continue
        if not (entry / "SKILL.md").exists():
            continue

        dest = paths.custom_dir / skill_id
        if dest.exists():
            raise SkillmProfileError(
                f"Cannot ingest '{skill_id}' from profile '{profile}': "
                f"destination {dest} already exists."
            )

        paths.custom_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(entry, dest)
        shutil.rmtree(entry)

        run_skillm(["install", str(dest), "--global", "--yes"])

        manifest = load_manifest(paths.profiles_toml)
        if profile in manifest and skill_id not in manifest[profile]:
            manifest[profile].append(skill_id)
            save_manifest(paths.profiles_toml, manifest)

        ingested.append(skill_id)

    return ingested


def sync_profile(paths: ProfilePaths, profile: str) -> dict[str, list[str]]:
    manifest = load_manifest(paths.profiles_toml)
    if profile not in manifest:
        raise SkillmProfileError(f"Profile '{profile}' does not exist.")

    ingested = ingest_untracked_skills(paths, profile)
    manifest = load_manifest(paths.profiles_toml)
    desired = manifest[profile]

    _validate_global_skills(desired)

    target_dir = paths.profile_dir(profile)
    target_dir.mkdir(parents=True, exist_ok=True)

    current = set(get_project_skills(target_dir))
    desired_set = set(desired)

    to_install = [s for s in desired if s not in current]
    to_remove = sorted(current - desired_set)

    for skill in to_install:
        try:
            run_skillm(["install", skill, "--project", str(target_dir), "--yes"])
        except SkillmCommandError as exc:
            raise SkillmProfileError(
                f"Failed installing '{skill}' into '{profile}': {exc}"
            ) from exc

    for skill in to_remove:
        try:
            run_skillm(["uninstall", skill, "--project", str(target_dir), "--yes"])
        except SkillmCommandError as exc:
            raise SkillmProfileError(f"Failed removing '{skill}' from '{profile}': {exc}") from exc

    return {
        "ingested": ingested,
        "installed": to_install,
        "removed": to_remove,
    }


def sync_all(paths: ProfilePaths) -> dict[str, dict[str, list[str]]]:
    manifest = load_manifest(paths.profiles_toml)
    return {profile: sync_profile(paths, profile) for profile in manifest}


def bootstrap_workspace(
    config: AppConfig,
    paths: ProfilePaths,
    source: str | None = None,
    force: bool = False,
) -> dict[str, object]:
    resolved_source = (source or config.collection_remote or str(config.collection_path)).strip()
    cmd = ["install", resolved_source, "--global", "--all", "--yes"]
    if force:
        cmd.append("--force")

    try:
        run_skillm(cmd)
    except SkillmCommandError as exc:
        raise SkillmProfileError(
            f"Bootstrap failed while installing global skills from '{resolved_source}': {exc}"
        ) from exc

    synced = sync_all(paths)
    return {
        "source": resolved_source,
        "profiles": synced,
    }
