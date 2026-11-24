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
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("SKILLM_PROFILE_CONFIG", raising=False)
    monkeypatch.setattr("skillm_profile.cli.shutil.which", lambda _: "/usr/local/bin/skillm")
    return fake_home


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    collection_dir = tmp_path / "skills-collection"
    collection_dir.mkdir()
    profiles_dir = tmp_path / "skillm" / "profiles"
    config_path = tmp_path / "xdg" / "skillm-profile" / "skillm-profile.toml"

    monkeypatch.setenv("SKILLM_PROFILE_CONFIG", str(config_path))

    config = AppConfig(
        collection_path=collection_dir,
        collection_remote="tinchogb/skills-collection",
        profiles_dir=profiles_dir,
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

    bad_profiles_dir = tmp_path / "bad_profiles_dir.toml"
    bad_profiles_dir.write_text(
        f'[collection]\npath = "{tmp_path}"\n[paths]\nprofiles_dir = 123\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="paths.profiles_dir"):
        load_config(bad_profiles_dir)


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


def test_init_strict_validations_and_interactive_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    # 1. Fails immediately if 'skillm' binary is not in PATH
    monkeypatch.setattr("skillm_profile.cli.shutil.which", lambda _: None)
    assert main(["init"]) == 1
    assert "not found in PATH" in capsys.readouterr().err
    monkeypatch.setattr("skillm_profile.cli.shutil.which", lambda _: "/usr/local/bin/skillm")

    # 2. --yes requires --collection-dir
    assert main(["init", "--yes"]) == 1
    assert "--collection-dir is required" in capsys.readouterr().err

    # 3. Non-existent collection dir fails
    missing_col = tmp_path / "does-not-exist"
    assert main(["init", "--collection-dir", str(missing_col), "--yes"]) == 1
    assert "does not exist" in capsys.readouterr().err

    # 4. Interactive flow with non-XDG config path and custom profiles_dir
    existing_col = tmp_path / "my-skills"
    existing_col.mkdir()
    custom_profiles = tmp_path / "custom-profiles"
    custom_cfg = tmp_path / "custom-location" / "skillm-profile.toml"

    prompts: list[str] = []

    def fake_input(prompt_text: str) -> str:
        prompts.append(prompt_text)
        mapping = {
            0: str(existing_col),
            1: "",  # skip optional remote
            2: str(custom_profiles),
            3: str(custom_cfg),
        }
        return mapping[len(prompts) - 1]

    monkeypatch.setattr("builtins.input", fake_input)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "press Enter to skip" in prompts[1]
    assert "export SKILLM_PROFILE_CONFIG=" in out

    loaded = load_config(custom_cfg)
    assert loaded.collection_path == existing_col.resolve()
    assert loaded.collection_remote == ""
    assert loaded.profiles_dir == custom_profiles.resolve()

    # Verify profiles_dir is stored under [paths] in the TOML file
    raw_toml = custom_cfg.read_text(encoding="utf-8")
    assert "[paths]" in raw_toml
    assert f'profiles_dir = "{custom_profiles.resolve()}"' in raw_toml
    assert "[skillm]" not in raw_toml

    # 5. Re-running without --force fails when config or profiles.toml exists
    assert (
        main(
            [
                "--config",
                str(custom_cfg),
                "init",
                "--collection-dir",
                str(existing_col),
                "--yes",
            ]
        )
        == 1
    )
    assert "already exists" in capsys.readouterr().err

    # 6. Re-running with --force succeeds and keeps existing profiles.toml
    (existing_col / "profiles.toml").write_text('[profiles]\ndev = ["a"]\n', encoding="utf-8")
    assert (
        main(
            [
                "--config",
                str(custom_cfg),
                "init",
                "--collection-dir",
                str(existing_col),
                "--profiles-dir",
                str(custom_profiles),
                "--yes",
                "--force",
            ]
        )
        == 0
    )
    assert "Kept existing" in capsys.readouterr().out

    # 7. Interactive with XDG default -> no export hint
    xdg_col = tmp_path / "xdg-skills"
    xdg_col.mkdir()
    answers_xdg = iter([str(xdg_col), "alice/skills", "", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers_xdg))
    assert main(["init"]) == 0
    assert "export SKILLM_PROFILE_CONFIG=" not in capsys.readouterr().out


def test_init_filesystem_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    col = tmp_path / "col"
    col.mkdir()

    # Empty input validation
    monkeypatch.setattr("builtins.input", lambda _: "   ")
    assert main(["init"]) == 1

    # Target config path is an existing directory
    dir_as_cfg = tmp_path / "cfg_dir"
    dir_as_cfg.mkdir()
    assert (
        main(
            ["--config", str(dir_as_cfg), "init", "--collection-dir", str(col), "--yes", "--force"]
        )
        == 1
    )

    # Target profiles_dir is an existing file
    file_as_profiles = tmp_path / "profiles_file"
    file_as_profiles.write_text("x", encoding="utf-8")
    assert (
        main(
            [
                "--config",
                str(tmp_path / "c1.toml"),
                "init",
                "--collection-dir",
                str(col),
                "--profiles-dir",
                str(file_as_profiles),
                "--yes",
            ]
        )
        == 1
    )

    # Unwritable parent directories (simulated via os.access)
    monkeypatch.setattr("skillm_profile.cli.os.access", lambda *_: False)
    assert (
        main(
            [
                "--config",
                str(tmp_path / "no_write" / "c.toml"),
                "init",
                "--collection-dir",
                str(col),
                "--yes",
            ]
        )
        == 1
    )
    monkeypatch.setattr("skillm_profile.cli.os.access", lambda *_: True)

    # Direct init_workspace guards
    bad_cfg = AppConfig(collection_path=tmp_path / "missing_col")
    with pytest.raises(SkillmProfileError, match="Collection directory does not exist"):
        init_workspace(bad_cfg, tmp_path / "out.toml")

    valid_cfg = AppConfig(collection_path=col, profiles_dir=tmp_path / "p_dir")
    with pytest.raises(SkillmProfileError, match="is a directory"):
        init_workspace(valid_cfg, dir_as_cfg, force=True)

    (col / "profiles.toml").write_text("[profiles]\n", encoding="utf-8")
    with pytest.raises(SkillmProfileError, match="Profiles manifest already exists"):
        init_workspace(valid_cfg, tmp_path / "fresh.toml", force=False)

    bad_pdir_cfg = AppConfig(collection_path=col, profiles_dir=file_as_profiles)
    with pytest.raises(SkillmProfileError, match="not a directory"):
        init_workspace(bad_pdir_cfg, tmp_path / "fresh2.toml", force=True)

    monkeypatch.setattr("skillm_profile.core.os.access", lambda *_: False)
    with pytest.raises(SkillmProfileError, match="Cannot write"):
        init_workspace(valid_cfg, tmp_path / "nw" / "fresh3.toml", force=True)

    unwritable_root = tmp_path / "locked_root"
    unwritable_root.mkdir()
    monkeypatch.setattr(
        "skillm_profile.core.os.access", lambda path, mode: Path(path) != unwritable_root
    )
    locked_pdir_cfg = AppConfig(
        collection_path=col, profiles_dir=unwritable_root / "nested" / "profiles"
    )
    with pytest.raises(SkillmProfileError, match="Cannot create profiles directory"):
        init_workspace(locked_pdir_cfg, tmp_path / "ok.toml", force=True)


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
    collection_dir.mkdir()
    profiles_dir = tmp_path / "skillm" / "profiles"
    config_path = tmp_path / "xdg" / "skillm-profile" / "skillm-profile.toml"
    monkeypatch.setenv("SKILLM_PROFILE_CONFIG", str(config_path))

    config = AppConfig(
        collection_path=collection_dir,
        collection_remote="tinchogb/skills-collection",
        profiles_dir=profiles_dir,
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
        profiles_dir=profiles_dir,
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


def test_init_rejects_blank_path_arguments(tmp_path: Path):
    col = tmp_path / "col"
    col.mkdir()

    assert (
        main(
            [
                "--config",
                str(tmp_path / "c1.toml"),
                "init",
                "--collection-dir",
                str(col),
                "--profiles-dir",
                "   ",
                "--yes",
            ]
        )
        == 1
    )
    assert main(["--config", "   ", "init", "--collection-dir", str(col), "--yes"]) == 1


def test_init_rejects_readonly_or_invalid_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    col = tmp_path / "col"
    col.mkdir()

    readonly_profiles = tmp_path / "ro_profiles"
    readonly_profiles.mkdir()
    monkeypatch.setattr(
        "skillm_profile.cli.os.access", lambda path, mode: Path(path) != readonly_profiles
    )
    assert (
        main(
            [
                "--config",
                str(tmp_path / "c_ro.toml"),
                "init",
                "--collection-dir",
                str(col),
                "--profiles-dir",
                str(readonly_profiles),
                "--yes",
            ]
        )
        == 1
    )
    monkeypatch.setattr("skillm_profile.cli.os.access", lambda *_: True)

    dir_cfg = tmp_path / "dir_as_cfg"
    dir_cfg.mkdir()
    assert (
        main(["--config", str(dir_cfg), "init", "--collection-dir", str(col), "--yes", "--force"])
        == 1
    )


def test_init_workspace_refuses_to_overwrite_existing_config(tmp_path: Path):
    col = tmp_path / "col"
    col.mkdir()
    existing_cfg = tmp_path / "already.toml"
    existing_cfg.write_text("[collection]\n", encoding="utf-8")

    cfg = AppConfig(collection_path=col, profiles_dir=tmp_path / "p_out")
    with pytest.raises(SkillmProfileError, match="Config file already exists"):
        init_workspace(cfg, existing_cfg, force=False)


def test_init_rejects_unwritable_config_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    col = tmp_path / "col"
    col.mkdir()
    writable_profiles = tmp_path / "profiles"
    locked_cfg_parent = tmp_path / "locked_cfg_dir"
    locked_cfg_parent.mkdir()

    monkeypatch.setattr(
        "skillm_profile.cli.os.access",
        lambda path, mode: Path(path) != locked_cfg_parent,
    )

    assert (
        main(
            [
                "--config",
                str(locked_cfg_parent / "skillm-profile.toml"),
                "init",
                "--collection-dir",
                str(col),
                "--profiles-dir",
                str(writable_profiles),
                "--yes",
            ]
        )
        == 1
    )
