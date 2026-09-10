"""Discover and load Claude Skills from disk.

A skill is a directory containing SKILL.md with YAML frontmatter (`name`,
`description`) and a Markdown body. Sibling .md files in the same directory are
reference material and get folded into the loaded text — that is how a real
skill's references reach the model.
"""

from __future__ import annotations

import logging

import yaml

from . import config

log = logging.getLogger(__name__)


class SkillError(RuntimeError):
    pass


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2].lstrip("\n")


def list_skills() -> list[dict]:
    """Scan SKILLS_DIR for */SKILL.md. Re-scanned per request, so dropping in a
    new skill folder takes effect without a restart."""
    skills: list[dict] = []
    if not config.SKILLS_DIR.is_dir():
        return skills

    for entry in sorted(config.SKILLS_DIR.iterdir()):
        skill_md = entry / "SKILL.md"
        if not (entry.is_dir() and skill_md.is_file()):
            continue
        try:
            meta, _body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except SkillError as exc:
            log.warning("Skipping skill %s: %s", entry.name, exc)
            continue
        skills.append({
            "id": entry.name,
            "name": meta.get("name") or entry.name,
            "description": meta.get("description") or "",
        })
    return skills


def load_skill(skill_id: str) -> dict:
    """Return {id, name, description, text} with reference files appended."""
    directory = config.SKILLS_DIR / skill_id
    skill_md = directory / "SKILL.md"

    # Guard against a crafted id escaping the skills directory.
    if directory.resolve().parent != config.SKILLS_DIR.resolve():
        raise SkillError(f"Invalid skill id {skill_id!r}")
    if not skill_md.is_file():
        raise SkillError(f"Skill {skill_id!r} has no SKILL.md")

    meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    sections = [body.strip()]

    for extra in sorted(directory.glob("*.md")):
        if extra.name == "SKILL.md":
            continue
        sections.append(
            f"## Reference: {extra.name}\n\n{extra.read_text(encoding='utf-8').strip()}"
        )

    return {
        "id": skill_id,
        "name": meta.get("name") or skill_id,
        "description": meta.get("description") or "",
        "text": "\n\n---\n\n".join(sections),
    }
