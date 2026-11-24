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


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("SKILLM_PROFILE_CONFIG", raising=False)
    return fake_home


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
    # 1. Interactive with non-XDG config path -> prints export hint
    custom_collection = tmp_path / "my-skills"
    custom_cfg = tmp_path / "custom-location" / "skillm-profile.toml"
    answers = iter([str(custom_collection), "alice/skills-collection", str(custom_cfg)])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert custom_cfg.exists()
    assert "export SKILLM_PROFILE_CONFIG=" in out

    # Re-running init without --force fails
    assert main(["--config", str(custom_cfg), "init", "--yes"]) == 1
    assert "already exists" in capsys.readouterr().err

    # Re-running with --force preserves existing collection_path and keeps profiles.toml
    (custom_collection / "profiles.toml").write_text('[profiles]\ndev = ["a"]\n', encoding="utf-8")
    assert main(["--config", str(custom_cfg), "init", "--yes", "--force"]) == 0
    assert "Kept existing" in capsys.readouterr().out

    # 2. Interactive with XDG default -> no export hint
    xdg_collection = tmp_path / "xdg-skills"
    answers_xdg = iter([str(xdg_collection), "", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers_xdg))
    assert main(["init"]) == 0
    assert "export SKILLM_PROFILE_CONFIG=" not in capsys.readouterr().out


def test_init_git_remote_detection_and_corrupt_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_dir = tmp_path / "user-skills"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    cfg_path = tmp_path / "cfg" / "skillm-profile.toml"

    # 1. Auto-detects GitHub owner/repo from git remote origin
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 0, stdout="git@github.com:alice/my-skills.git\n", stderr=""
        ),
    )
    assert (
        main(["--config", str(cfg_path), "init", "--collection-dir", str(repo_dir), "--yes"]) == 0
    )
    loaded = load_config(cfg_path)
    assert loaded.collection_path == repo_dir.resolve()
    assert loaded.collection_remote == "alice/my-skills"

    # 2. Gracefully returns empty remote when git remote fails
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr="no remote"),
    )
    assert (
        main(
            [
                "--config",
                str(cfg_path),
                "init",
                "--collection-dir",
                str(repo_dir),
                "--yes",
                "--force",
            ]
        )
        == 0
    )
    assert load_config(cfg_path).collection_remote == ""

    # 3. Corrupt existing config falls back to ~/skills-collection inside isolated HOME
    corrupt_cfg = tmp_path / "corrupt.toml"
    corrupt_cfg.write_text("not = [valid", encoding="utf-8")
    assert main(["--config", str(corrupt_cfg), "init", "--yes", "--force"]) == 0
    assert load_config(corrupt_cfg).collection_path == (Path.home() / "skills-collection").resolve()


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
        cmd, skill, *_ = args
        if cmd == "install":
            installed_in_project.add(skill)
        elif cmd == "uninstall":
            installed_in_project.discard(skill)
        return ""

    monkeypatch.setattr("skillm_profile.core.run_skillm", fake_run_skillm)

    assert main(["list"]) == 0
    assert "No profiles defined." in capsys.readouterr().out

    assert main(["create", "research"]) == 0
    assert main(["create", "research"]) == 1
    assert "already exists" in capsys.readouterr().err

    assert main(["add", "research", "zotero-cli", "zotero-library-curator"]) == 0
    assert paths.profile_lock_file("research").name == "skills-lock.json"

    assert main(["list"]) == 0
    assert "research: zotero-cli, zotero-library-curator" in capsys.readouterr().out

    assert main(["sync", "research"]) == 0
    assert main(["sync", "--all"]) == 0

    with pytest.raises(SystemExit):
        main(["sync"])

    assert main(["remove", "research", "zotero-cli"]) == 0
    assert installed_in_project == {"zotero-library-curator"}

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

    (skills_dir / "README.txt").write_text("ignore", encoding="utf-8")
    (skills_dir / "not-a-skill").mkdir()

    untracked_dir = skills_dir / "new-local-skill"
    untracked_dir.mkdir()
    (untracked_dir / "SKILL.md").write_text("# New Skill\n", encoding="utf-8")

    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: [])
    monkeypatch.setattr("skillm_profile.core.get_global_skills", lambda: ["new-local-skill"])
    monkeypatch.setattr("skillm_profile.core.run_skillm", lambda _: "")

    summary = sync_profile(paths, "research")
    assert summary["ingested"] == ["new-local-skill"]
    assert (paths.custom_dir / "new-local-skill" / "SKILL.md").exists()

    untracked_dir.mkdir()
    (untracked_dir / "SKILL.md").write_text("# Duplicate\n", encoding="utf-8")
    with pytest.raises(SkillmProfileError, match="already exists"):
        sync_profile(paths, "research")


def test_runner_parsing_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("skillm")

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    with pytest.raises(SkillmCommandError, match="not found in PATH"):
        run_skillm(["list"])

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr="bad flag"),
    )
    with pytest.raises(SkillmCommandError, match="bad flag"):
        run_skillm(["bad"])

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="{not-json", stderr=""),
    )
    with pytest.raises(SkillmCommandError, match="Failed to parse"):
        get_global_skills()

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

    create_profile(paths, "empty-prof")
    monkeypatch.setattr("skillm_profile.core.get_project_skills", lambda _: [])
    assert sync_profile(paths, "empty-prof") == {
        "ingested": [],
        "installed": [],
        "removed": [],
    }

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

    local_only_cfg = AppConfig(
        collection_path=collection_dir,
        collection_remote="",
        skillm_profiles_dir=profiles_dir,
    )
    init_workspace(local_only_cfg, config_path, force=True)
    recorded.clear()

    assert main(["bootstrap"]) == 0
    assert recorded == [["install", str(collection_dir), "--global", "--all", "--yes"]]

    def fail_bootstrap(args: list[str]) -> str:
        raise SkillmCommandError("network unreachable")

    monkeypatch.setattr("skillm_profile.core.run_skillm", fail_bootstrap)
    assert main(["bootstrap"]) == 1
    assert "Bootstrap failed" in capsys.readouterr().err
