from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# Skills that matter most for Cursor behavior / this bridge.
PRIORITY_SKILL_NAMES = {
    "update-cursor-settings",
    "canvas",
    "create-rule",
    "create-skill",
    "create-hook",
    "shell",
    "statusline",
    "sdk",
    "review",
    "review-bugbot",
    "review-security",
    "automate",
    "autopilot",
    "loop",
    "split-to-prs",
    "migrate-to-skills",
    "onboard",
    "rename-chat",
    "cursor-guide",
}


@dataclass
class SkillInfo:
    name: str
    path: Path
    description: str
    source: str
    priority: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "source": self.source,
            "priority": self.priority,
        }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val == ">":
            continue
        if key == "description" and val.startswith("-"):
            continue
        meta[key] = val
    # Multi-line description under description: >-
    if "description" not in meta:
        block = m.group(1)
        if "description:" in block:
            after = block.split("description:", 1)[1]
            lines = []
            for line in after.splitlines()[1:]:
                if line and not line[0].isspace() and ":" in line:
                    break
                lines.append(line.strip())
            desc = " ".join(x for x in lines if x).strip()
            if desc:
                meta["description"] = desc
    body = text[m.end() :]
    return meta, body


def discover_skill_roots(settings: Settings | None = None) -> list[tuple[str, Path]]:
    s = settings or get_settings()
    roots: list[tuple[str, Path]] = []
    candidates = [
        ("cursor-skills", s.cursor_home / "skills-cursor"),
        ("cursor-skills-user", s.cursor_home / "skills"),
        ("agents-skills", s.agents_home / "skills"),
        ("claude-skills", s.claude_home / "skills"),
        ("codex-skills", Path.home() / ".codex" / "skills"),
    ]
    for label, path in candidates:
        if path.is_dir():
            roots.append((label, path))
    # Plugin-cached skills (cloudflare, azure, etc.)
    plugin_cache = s.cursor_home / "plugins" / "cache"
    if plugin_cache.is_dir():
        for skill_md in plugin_cache.glob("**/skills/*/SKILL.md"):
            # parent of SKILL.md is skill dir; group under plugin-skills
            roots.append(("plugin-skills", skill_md.parent.parent))
            break
        # Collect unique parent skill directories instead
    return roots


def _iter_skill_files(settings: Settings | None = None) -> list[tuple[str, Path]]:
    s = settings or get_settings()
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    roots = [
        ("cursor-skills", s.cursor_home / "skills-cursor"),
        ("cursor-skills-user", s.cursor_home / "skills"),
        ("agents-skills", s.agents_home / "skills"),
        ("claude-skills", s.claude_home / "skills"),
        ("codex-skills", Path.home() / ".codex" / "skills"),
    ]
    for source, root in roots:
        if not root.is_dir():
            continue
        for skill_md in root.glob("*/SKILL.md"):
            rp = skill_md.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            found.append((source, skill_md))

    plugin_cache = s.cursor_home / "plugins" / "cache"
    if plugin_cache.is_dir():
        for skill_md in plugin_cache.glob("**/SKILL.md"):
            rp = skill_md.resolve()
            if rp in seen:
                continue
            # Skip deep nested duplicates / huge trees lightly
            seen.add(rp)
            found.append(("plugin-skills", skill_md))

    return found


def list_skills(settings: Settings | None = None) -> list[SkillInfo]:
    skills: list[SkillInfo] = []
    for source, path in _iter_skill_files(settings):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(text)
        name = str(meta.get("name") or path.parent.name)
        desc = str(meta.get("description") or "").strip()
        if not desc:
            # first non-empty body line
            for line in text.splitlines():
                if line.strip() and not line.startswith("#") and not line.startswith("---"):
                    desc = line.strip()[:200]
                    break
        skills.append(
            SkillInfo(
                name=name,
                path=path,
                description=desc[:400],
                source=source,
                priority=name in PRIORITY_SKILL_NAMES or source == "cursor-skills",
            )
        )
    skills.sort(key=lambda x: (not x.priority, x.source, x.name.lower()))
    return skills


def load_skill_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_skills_context(
    settings: Settings | None = None,
    *,
    include_full_priority: bool = True,
    max_chars: int | None = None,
) -> str:
    """Build a system-prompt block with skill catalog + full priority skill bodies."""
    s = settings or get_settings()
    budget = max_chars if max_chars is not None else s.skills_max_chars
    skills = list_skills(s)

    lines: list[str] = [
        "# Imported Cursor / agent skills",
        "",
        "You are running outside Cursor's built-in agent, but you MUST follow these skills",
        "when relevant. Prefer reading a skill by path if you need full detail beyond this pack.",
        "",
        "## Skill catalog",
        "",
    ]
    for sk in skills:
        flag = " [priority]" if sk.priority else ""
        lines.append(f"- **{sk.name}**{flag} (`{sk.source}`): {sk.description}")
        lines.append(f"  Path: `{sk.path}`")

    used = len("\n".join(lines))
    if include_full_priority:
        lines.extend(["", "## Priority skill bodies", ""])
        for sk in skills:
            if not sk.priority:
                continue
            try:
                body = load_skill_text(sk.path)
            except OSError:
                continue
            chunk = f"\n### Skill: {sk.name}\nSource: {sk.path}\n\n{body}\n"
            if used + len(chunk) > budget:
                lines.append(
                    f"\n_(Truncated remaining priority skills to stay under {budget} chars.)_\n"
                )
                break
            lines.append(chunk)
            used += len(chunk)

    text = "\n".join(lines)
    if len(text) > budget:
        text = text[: budget - 80] + "\n\n_(skills context truncated)_\n"
    return text


def cursor_settings_snapshot(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    path = s.settings_json
    if not path.exists():
        return {"path": str(path), "exists": False, "settings": {}}
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Strip // comments lightly
    cleaned = re.sub(r"//.*?$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # fallback: empty
        data = {}
    # Keep Cursor-relevant keys
    relevant_prefixes = (
        "cursor.",
        "aipopup.",
        "editor.",
        "files.",
        "terminal.",
        "workbench.",
        "window.",
        "git.",
    )
    filtered = {
        k: v
        for k, v in data.items()
        if isinstance(k, str)
        and (k.startswith(relevant_prefixes) or k in {"http.proxy", "http.noProxy"})
    }
    return {"path": str(path), "exists": True, "settings": filtered, "all_key_count": len(data)}


def build_settings_context(settings: Settings | None = None) -> str:
    snap = cursor_settings_snapshot(settings)
    if not snap.get("exists"):
        return "# Cursor settings\n\nNo settings.json found.\n"
    body = json.dumps(snap["settings"], indent=2, ensure_ascii=False)
    return (
        "# Relevant Cursor settings (from settings.json)\n\n"
        f"Path: `{snap['path']}`\n\n"
        "These settings describe how Cursor is configured on this machine. "
        "Respect them when advising Cursor workflow or editing settings.\n\n"
        f"```json\n{body}\n```\n"
    )


def find_skill_by_name(name: str, settings: Settings | None = None) -> SkillInfo | None:
    needle = name.strip().lstrip("/").lower()
    if not needle:
        return None
    for sk in list_skills(settings):
        if sk.name.lower() == needle:
            return sk
    for sk in list_skills(settings):
        if sk.name.lower().startswith(needle):
            return sk
    return None


def parse_slash_command(message: str) -> tuple[str | None, str]:
    """Return (skill_name, remainder) if message starts with /skill…"""
    text = (message or "").strip()
    if not text.startswith("/"):
        return None, text
    # /skill-name rest of message
    first, _, rest = text[1:].partition(" ")
    skill = first.strip().strip("/")
    if not skill or skill.startswith("/"):
        return None, text
    return skill, rest.strip()


def build_skill_injection(skill_name: str, settings: Settings | None = None) -> str:
    sk = find_skill_by_name(skill_name, settings)
    if not sk:
        return f"# Requested skill `/{skill_name}`\n\nSkill not found.\n"
    try:
        body = load_skill_text(sk.path)
    except OSError as e:
        return f"# Requested skill `/{skill_name}`\n\nCould not read skill: {e}\n"
    return (
        f"# Active skill: {sk.name}\n"
        f"Source: `{sk.path}`\n\n"
        f"Follow this skill for the user's current request.\n\n{body}\n"
    )


def build_system_preamble(
    settings: Settings | None = None,
    *,
    include_cursor_settings: bool = True,
    include_skills_catalog: bool = True,
    extra_blocks: list[str] | None = None,
    active_skill: str | None = None,
) -> str:
    s = settings or get_settings()
    parts = [
        "You are a coding assistant in Portage.",
        "You may run on Microsoft Foundry or AWS Bedrock depending on the user's connection.",
        "If a Cursor conversation is linked, preserve continuity. If not, treat this as a standalone chat.",
        "When the user invokes a /skill, follow that skill closely.",
        "",
        "Important behavior:",
        "- Prefer actionable answers.",
        "- Respect linked workspace paths when provided.",
        "- Do not invent write-back success; only claim Cursor sync when it happened.",
        "",
    ]
    if include_cursor_settings:
        parts.extend([build_settings_context(s), ""])
    if include_skills_catalog:
        parts.extend([build_skills_context(s), ""])
    if active_skill:
        parts.extend([build_skill_injection(active_skill, s), ""])
    if extra_blocks:
        for block in extra_blocks:
            if block and block.strip():
                parts.extend([block.strip(), ""])
    return "\n".join(parts)
