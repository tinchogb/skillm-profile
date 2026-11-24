import tomllib
from pathlib import Path

import tomli_w


class ManifestError(RuntimeError):
    """Raised when profiles.toml is missing, malformed, or structurally invalid."""


def load_manifest(manifest_path: Path) -> dict[str, list[str]]:
    if not manifest_path.exists():
        raise ManifestError(
            f"Profiles manifest not found at {manifest_path}. Run 'skillm-profile init' first."
        )
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"Malformed profiles manifest {manifest_path}: {exc}") from exc

    raw_profiles = data.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ManifestError(f"Invalid '[profiles]' table in {manifest_path}.")

    profiles: dict[str, list[str]] = {}
    for name, skills in raw_profiles.items():
        if (
            not isinstance(name, str)
            or not isinstance(skills, list)
            or not all(isinstance(s, str) for s in skills)
        ):
            raise ManifestError(f"Invalid skill list for profile '{name}' in {manifest_path}.")
        profiles[name] = list(skills)

    return profiles


def save_manifest(manifest_path: Path, profiles: dict[str, list[str]]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {name: list(profiles[name]) for name in sorted(profiles)}
    manifest_path.write_text(tomli_w.dumps({"profiles": ordered}), encoding="utf-8")
