import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or is invalid."""


def default_xdg_config_path() -> Path:
    xdg_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return base / "skillm-profile" / "skillm-profile.toml"


def resolve_config_path(cli_config: str | Path | None = None) -> Path:
    if cli_config:
        return Path(cli_config).expanduser().resolve()
    env_config = os.environ.get("SKILLM_PROFILE_CONFIG", "").strip()
    if env_config:
        return Path(env_config).expanduser().resolve()
    return default_xdg_config_path().resolve()


@dataclass(frozen=True)
class AppConfig:
    collection_path: Path
    collection_remote: str = ""
    profiles_manifest: str = "profiles.toml"
    custom_dir: str = "custom"
    third_party_dir: str = "third-party"
    skillm_profiles_dir: Path = Path.home() / ".skillm" / "profiles"

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        collection = data.get("collection", {})
        paths = data.get("paths", {})
        skillm = data.get("skillm", {})

        raw_collection_path = collection.get("path")
        if not raw_collection_path or not isinstance(raw_collection_path, str):
            raise ConfigError("Missing or invalid 'collection.path' in configuration.")

        raw_profiles_dir = skillm.get("profiles_dir", str(Path.home() / ".skillm" / "profiles"))

        return cls(
            collection_path=Path(raw_collection_path).expanduser().resolve(),
            collection_remote=str(collection.get("remote", "")).strip(),
            profiles_manifest=str(paths.get("profiles_manifest", "profiles.toml")).strip(),
            custom_dir=str(paths.get("custom_dir", "custom")).strip(),
            third_party_dir=str(paths.get("third_party_dir", "third-party")).strip(),
            skillm_profiles_dir=Path(raw_profiles_dir).expanduser().resolve(),
        )

    def to_dict(self) -> dict:
        return {
            "collection": {
                "path": str(self.collection_path),
                "remote": self.collection_remote,
            },
            "paths": {
                "profiles_manifest": self.profiles_manifest,
                "custom_dir": self.custom_dir,
                "third_party_dir": self.third_party_dir,
            },
            "skillm": {
                "profiles_dir": str(self.skillm_profiles_dir),
            },
        }

    def to_toml(self) -> str:
        return tomli_w.dumps(self.to_dict())


def load_config(config_path: Path) -> AppConfig:
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file not found at {config_path}. "
            "Run 'skillm-profile init' to create it."
        )
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed configuration file {config_path}: {exc}") from exc

    return AppConfig.from_dict(data)


def save_config(config: AppConfig, config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.to_toml(), encoding="utf-8")
