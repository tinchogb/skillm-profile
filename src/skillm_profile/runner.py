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
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillmCommandError(f"Failed to parse 'skillm list --json' output: {exc}") from exc

    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("skills"), list):
        return data["skills"]
    if isinstance(payload.get("skills"), list):
        return payload["skills"]
    return []


def get_global_skills() -> list[str]:
    result: list[str] = []
    for item in _list_skills_data():
        if not isinstance(item, dict):
            continue
        skill_id = item.get("id")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        installs = item.get("installs", [])
        if isinstance(installs, list) and any(
            isinstance(inst, dict) and inst.get("scope") == "global" for inst in installs
        ):
            result.append(skill_id)
    return sorted(set(result))


def get_project_skills(project_dir: Path) -> list[str]:
    target_root = project_dir.expanduser().resolve()
    expected_agents_dir = target_root / ".agents" / "skills"
    result: list[str] = []
    for item in _list_skills_data():
        if not isinstance(item, dict):
            continue
        skill_id = item.get("id")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        installs = item.get("installs", [])
        if not isinstance(installs, list):
            continue
        for inst in installs:
            if not isinstance(inst, dict) or inst.get("scope") == "global":
                continue
            raw_path = inst.get("path")
            if isinstance(raw_path, str) and raw_path:
                inst_path = Path(raw_path).expanduser().resolve()
                if (
                    inst_path == expected_agents_dir / skill_id
                    or inst_path.parent == expected_agents_dir
                ):
                    result.append(skill_id)
                    break
    return sorted(set(result))
