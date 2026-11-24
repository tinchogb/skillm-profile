import json
import runpy
import subprocess
from pathlib import Path

import pytest

from skillm_profile.cli import main
from skillm_profile.config import (
    AppConfig,
    ConfigError,
    load_config,
    resolve_config_path,
)
from skillm_profile.core import (
    SkillmProfileError,
    add_skills,
    create_profile,
    init_workspace,
    remove_skills,
    sync_all,
    sync_profile,
)
from skillm_profile.manifest import ManifestError, load_manifest, save_manifest
from skillm_profile.paths import ProfilePaths
from skillm_profile.runner import (
    SkillmCommandError,
    get_global_skills,
    get_project_skills,
    run_skillm,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    collection_dir = tmp_path / "skills-collection"
    profiles_dir = tmp_path / "skillm" / "profiles"
    config_path = tmp_path / "xdg" / "skillm-profile" / "skillm-profile.toml"

    monkeypatch.setenv("SKILLM_PROFILE_CONFIG", str(config_path))

    config = AppConfig(
        collection_path=collection_dir,
        collection_remote="tinchogb/skills-collection",
        skillm_profiles_dir=profiles_dir,
    )
    init_workspace(config, config_path)
    paths = ProfilePaths.from_config(config)
    return {
        "config": config,
        "config_path": config_path,
        "paths": paths,
        "collection_dir": collection_dir,
        "profiles_dir": profiles_dir,
    }


def test_config_resolution_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    xdg = tmp_path / "custom_xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("SKILLM_PROFILE_CONFIG", raising=False)

    assert resolve_config_path() == (xdg / "skillm-profile" / "skillm-profile.toml").resolve()

    env_cfg = tmp_path / "env.toml"
    monkeypatch.setenv("SKILLM_PROFILE_CONFIG", str(env_cfg))
    assert resolve_config_path() == env_cfg.resolve()

    cli_cfg = tmp_path / "cli.toml"
    assert resolve_config_path(cli_cfg) == cli_cfg.resolve()

    with pytest.raises(ConfigError):
        load_config(tmp_path / "nonexistent.toml")

    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("not = [valid toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed"):
        load_config(bad_toml)

    missing_path_toml = tmp_path / "missing_path.toml"
    missing_path_toml.write_text("[collection]\nremote = 'x/y'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="collection.path"):
        load_config(missing_path_toml)


def test_manifest_errors_and_roundtrip(tmp_path: Path):
    manifest_file = tmp_path / "profiles.toml"
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(manifest_file)

    manifest_file.write_text("[profiles\nbroken", encoding="utf-8")
    with pytest.raises(ManifestError, match="Malformed"):
        load_manifest(manifest_file)

    manifest_file.write_text('profiles = "not-a-table"\n', encoding="utf-8")
    with pytest.raises(ManifestError, match="Invalid '\\[profiles\\]'"):
        load_manifest(manifest_file)

    manifest_file.write_text('[profiles]\nresearch = "not-a-list"\n', encoding="utf-8")
    with pytest.raises(ManifestError, match="Invalid skill list"):
        load_manifest(manifest_file)

    save_manifest(manifest_file, {"b": ["s2"], "a": ["s1"]})
    assert load_manifest(manifest_file) == {"a": ["s1"], "b": ["s2"]}


def test_init_cli_interactive_and_non_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("SKILLM_PROFILE_CONFIG", raising=False)

    # 1. Interactive with non-XDG config path -> prints export hint
    custom_collection = tmp_path / "my-skills"
    custom_cfg = tmp_path / "custom-location" / "skillm-profile.toml"
    answers = iter([str(custom_collection), "tinchogb/skills-collection", str(custom_cfg)])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert custom_cfg.exists()
    assert "export SKILLM_PROFILE_CONFIG=" in out

    # Re-running init without --force fails
    assert main(["--config", str(custom_cfg), "init", "--yes"]) == 1
    assert "already exists" in capsys.readouterr().err

    # Re-running with --force succeeds and keeps existing profiles.toml
    (custom_collection / "profiles.toml").write_text('[profiles]\ndev = ["a"]\n', encoding="utf-8")
    assert main(["--config", str(custom_cfg), "init", "--yes", "--force"]) == 0
    assert "Kept existing" in capsys.readouterr().out

    # 2. Interactive with XDG default -> no export hint
    xdg_collection = tmp_path / "xdg-skills"
    answers_xdg = iter([str(xdg_collection), "", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers_xdg))
    assert main(["init"]) == 0
    assert "export SKILLM_PROFILE_CONFIG=" not in capsys.readouterr().out


def test_profile_lifecycle_and_cli_commands(
    workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths: ProfilePaths = workspace["paths"]
    installed_in_project: set[str] = set()

    monkeypatch.setattr(
        "skillm_profile.core.get_global_skills",
        lambda: ["zotero-cli", "zotero-library-curator"],
    )
    monkeypatch.setattr(
        "skillm_profile.core.get_project_skills",
        lambda _: sorted(installed_in_project),
    )

    def fake_run_skillm(args: list[str]) -> str:
        if args[0] == "install":
            installed_in_project.add(args[1])
        elif args[0] == "uninstall":
            installed_in_project.discard(args[1])
        return ""

    monkeypatch.setattr("skillm_profile.core.run_skillm", fake_run_skillm)

    # Empty list via CLI
    assert main(["list"]) == 0
    assert "No profiles defined." in capsys.readouterr().out

    # Create profile via CLI and test duplicate error
    assert main(["create", "research"]) == 0
    assert main(["create", "research"]) == 1
    assert "already exists" in capsys.readouterr().err

    # Add skills via CLI
    assert main(["add", "research", "zotero-cli", "zotero-library-curator"]) == 0
    assert paths.profile_lock_file("research").name == "skills-lock.json"

    # List populated profiles via CLI
    assert main(["list"]) == 0
    assert "research: zotero-cli, zotero-library-curator" in capsys.readouterr().out

    # Sync single and --all via CLI
    assert main(["sync", "research"]) == 0
    assert main(["sync", "--all"]) == 0

    with pytest.raises(SystemExit):
        main(["sync"])

    # Remove skill via CLI
    assert main(["remove", "research", "zotero-cli"]) == 0
    assert installed_in_project == {"zotero-library-curator"}

    # Delete profile with --purge via CLI and verify missing profile error
    assert main(["delete", "research", "--purge"]) == 0
    assert not paths.profile_dir("research").exists()
    assert main(["delete", "research"]) == 1


def test_core_edge_cases_and_errors(workspace, monkeypatch: pytest.MonkeyPatch):
    paths: ProfilePaths = workspace["paths"]
    monkeypatch.setattr("skillm_profile.core.get_global_skills", lambda: ["zotero-cli"])

    with pytest.raises(SkillmProfileError, match="does not exist"):
        add_skills(paths, "missing", ["zotero-cli"])

    with pytest.raises(SkillmProfileError, match="does not exist"):
        remove_skills(paths, "missing", ["zotero-cli"])

    with pytest.raises(SkillmProfileError, match="does not exist"):
        sync_profile(paths, "missing")

    create_profile(paths, "research")
    with pytest.raises(SkillmProfileError, match="not installed globally"):
        add_skills(paths, "research", ["nonexistent-skill"])

    # Simulate underlying skillm failure on install and uninstall
    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: [])

    def fail_install(args: list[str]) -> str:
        raise SkillmCommandError("boom")

    monkeypatch.setattr("skillm_profile.core.run_skillm", fail_install)
    with pytest.raises(SkillmProfileError, match="Failed installing"):
        add_skills(paths, "research", ["zotero-cli"])

    monkeypatch.setattr(
        "skillm_profile.core.get_project_skills", lambda _: ["zotero-cli", "stale-skill"]
    )
    with pytest.raises(SkillmProfileError, match="Failed removing"):
        sync_all(paths)


def test_ingest_untracked_skills(workspace, monkeypatch: pytest.MonkeyPatch):
    paths: ProfilePaths = workspace["paths"]
    create_profile(paths, "research")

    skills_dir = paths.profile_skills_dir("research")
    skills_dir.mkdir(parents=True)

    # Non-directory file and directory without SKILL.md should be ignored
    (skills_dir / "README.txt").write_text("ignore", encoding="utf-8")
    (skills_dir / "not-a-skill").mkdir()

    # Valid untracked skill
    untracked_dir = skills_dir / "new-local-skill"
    untracked_dir.mkdir()
    (untracked_dir / "SKILL.md").write_text("# New Skill\n", encoding="utf-8")

    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: [])
    monkeypatch.setattr("skillm_profile.core.get_global_skills", lambda: ["new-local-skill"])
    monkeypatch.setattr("skillm_profile.core.run_skillm", lambda _: "")

    summary = sync_profile(paths, "research")
    assert summary["ingested"] == ["new-local-skill"]
    assert (paths.custom_dir / "new-local-skill" / "SKILL.md").exists()

    # Re-creating same untracked skill when destination already exists in custom_dir fails
    untracked_dir.mkdir()
    (untracked_dir / "SKILL.md").write_text("# Duplicate\n", encoding="utf-8")
    with pytest.raises(SkillmProfileError, match="already exists"):
        sync_profile(paths, "research")


def test_runner_parsing_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Missing binary
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("skillm")

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    with pytest.raises(SkillmCommandError, match="not found in PATH"):
        run_skillm(["list"])

    # 2. Non-zero exit code
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr="bad flag"),
    )
    with pytest.raises(SkillmCommandError, match="bad flag"):
        run_skillm(["bad"])

    # 3. Invalid JSON
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="{not-json", stderr=""),
    )
    with pytest.raises(SkillmCommandError, match="Failed to parse"):
        get_global_skills()

    # 4. Empty output & unexpected JSON type
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="   ", stderr=""),
    )
    assert get_global_skills() == []

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout='"unexpected"', stderr=""),
    )
    assert get_global_skills() == []

    # 5. Valid payload parsing (both top-level list and {"skills": [...]})
    proj_dir = tmp_path / "profiles" / "research"
    payload = {
        "skills": [
            {"id": "skill-a", "scope": "global"},
            {"name": "skill-b", "installs": [{"global": True}, {"project": str(proj_dir)}]},
            {"id": "skill-c", "project": str(proj_dir)},
            {"no_id": True},
        ]
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=json.dumps(payload), stderr=""),
    )
    assert get_global_skills() == ["skill-a", "skill-b"]
    assert get_project_skills(proj_dir) == ["skill-b", "skill-c"]


def test_main_module_entrypoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sys.argv", ["skillm-profile", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("skillm_profile.__main__", run_name="__main__")
    assert exc.value.code == 0


def test_remaining_branches_for_full_coverage(workspace, monkeypatch: pytest.MonkeyPatch):
    paths: ProfilePaths = workspace["paths"]

    # 1. Syncing an empty profile exercises _validate_global_skills([]) early return (core.py:74)
    create_profile(paths, "empty-prof")
    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: [])
    assert sync_profile(paths, "empty-prof") == {
        "ingested": [],
        "installed": [],
        "removed": [],
    }

    # 2. Existing tracked skill inside profile_skills_dir is skipped by ingest (core.py:130)
    tracked_dir = paths.profile_skills_dir("empty-prof") / "already-tracked"
    tracked_dir.mkdir(parents=True)
    (tracked_dir / "SKILL.md").write_text("# Tracked\n", encoding="utf-8")

    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: ["already-tracked"])
    monkeypatch.setattr("skillm_profile.core.get_global_skills", lambda: ["already-tracked"])
    monkeypatch.setattr("skillm_profile.core.run_skillm", lambda _: "")
    assert sync_profile(paths, "empty-prof") == {
        "ingested": [],
        "installed": [],
        "removed": ["already-tracked"],
    }

    # 3. Top-level JSON list payload from 'skillm list --json' (runner.py:43)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 0, stdout=json.dumps([{"id": "list-skill", "global": True}]), stderr=""
        ),
    )
    assert get_global_skills() == ["list-skill"]


def test_cli_fallback_return_zero(workspace, monkeypatch: pytest.MonkeyPatch):
    class DummyParser:
        def parse_args(self, argv=None):
            import argparse

            return argparse.Namespace(config=None, command="noop")

    monkeypatch.setattr("skillm_profile.cli.build_parser", lambda: DummyParser())
    assert main([]) == 0


def test_bootstrap_command_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    collection_dir = tmp_path / "skills-collection"
    profiles_dir = tmp_path / "skillm" / "profiles"
    config_path = tmp_path / "xdg" / "skillm-profile" / "skillm-profile.toml"
    monkeypatch.setenv("SKILLM_PROFILE_CONFIG", str(config_path))

    # 1. With remote configured + --force
    config = AppConfig(
        collection_path=collection_dir,
        collection_remote="tinchogb/skills-collection",
        skillm_profiles_dir=profiles_dir,
    )
    init_workspace(config, config_path)
    paths = ProfilePaths.from_config(config)
    create_profile(paths, "research")

    recorded: list[list[str]] = []
    monkeypatch.setattr("skillm_profile.core.run_skillm", lambda args: recorded.append(args) or "")
    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: [])

    assert main(["bootstrap", "--force"]) == 0
    assert recorded == [
        ["install", "tinchogb/skills-collection", "--global", "--all", "--yes", "--force"]
    ]
    assert "Bootstrapped from 'tinchogb/skills-collection'" in capsys.readouterr().out

    # 2. Fallback to local collection_path when remote is empty
    local_only_cfg = AppConfig(
        collection_path=collection_dir,
        collection_remote="",
        skillm_profiles_dir=profiles_dir,
    )
    init_workspace(local_only_cfg, config_path, force=True)
    recorded.clear()

    assert main(["bootstrap"]) == 0
    assert recorded == [["install", str(collection_dir), "--global", "--all", "--yes"]]

    # 3. Underlying skillm failure surfaces as SkillmProfileError (exit code 1)
    def fail_bootstrap(args: list[str]) -> str:
        raise SkillmCommandError("network unreachable")

    monkeypatch.setattr("skillm_profile.core.run_skillm", fail_bootstrap)
    assert main(["bootstrap"]) == 1
    assert "Bootstrap failed" in capsys.readouterr().err
