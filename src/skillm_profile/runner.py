import json
import subprocess
from pathlib import Path


class SkillmCommandError(RuntimeError):
    """Raised when an underlying skillm CLI invocation fails."""


def run_skillm(args: list[str]) -> str:
    cmd = ["skillm", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SkillmCommandError(
            "The 'skillm' binary was not found in PATH. Install skillm first."
        ) from exc

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise SkillmCommandError(f"Command {' '.join(cmd)} failed: {detail}")

    return proc.stdout


def _list_skills_data() -> list[dict]:
    raw = run_skillm(["list", "--json"])
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillmCommandError(f"Failed to parse 'skillm list --json' output: {exc}") from exc

    if isinstance(data, dict) and "skills" in data:
        return list(data["skills"])
    if isinstance(data, list):
        return data
    return []


def get_global_skills() -> list[str]:
    result: list[str] = []
    for item in _list_skills_data():
        skill_id = item.get("id") or item.get("name")
        if not skill_id:
            continue
        if item.get("global") is True or item.get("scope") == "global":
            result.append(skill_id)
            continue
        installs = item.get("installs", [])
        if any(inst.get("scope") == "global" or inst.get("global") is True for inst in installs):
            result.append(skill_id)
    return sorted(set(result))


def get_project_skills(project_dir: Path) -> list[str]:
    target = str(project_dir.expanduser().resolve())
    result: list[str] = []
    for item in _list_skills_data():
        skill_id = item.get("id") or item.get("name")
        if not skill_id:
            continue
        for key in ("project", "path", "dir"):
            val = item.get(key)
            if val and str(Path(val).expanduser().resolve()) == target:
                result.append(skill_id)
                break
        installs = item.get("installs", [])
        for inst in installs:
            inst_path = inst.get("project") or inst.get("path") or inst.get("dir")
            if inst_path and str(Path(inst_path).expanduser().resolve()) == target:
                result.append(skill_id)
                break
    return sorted(set(result))
