from dataclasses import dataclass
from pathlib import Path

from skillm_profile.config import AppConfig


@dataclass(frozen=True)
class ProfilePaths:
    collection_dir: Path
    profiles_toml: Path
    custom_dir: Path
    third_party_dir: Path
    profiles_dir: Path

    @classmethod
    def from_config(cls, config: AppConfig) -> "ProfilePaths":
        return cls(
            collection_dir=config.collection_path,
            profiles_toml=config.collection_path / config.profiles_manifest,
            custom_dir=config.collection_path / config.custom_dir,
            third_party_dir=config.collection_path / config.third_party_dir,
            profiles_dir=config.profiles_dir,
        )

    def profile_dir(self, profile: str) -> Path:
        return self.profiles_dir / profile

    def profile_skills_dir(self, profile: str) -> Path:
        return self.profile_dir(profile) / ".agents" / "skills"

    def profile_lock_file(self, profile: str) -> Path:
        return self.profile_dir(profile) / "skills-lock.json"
