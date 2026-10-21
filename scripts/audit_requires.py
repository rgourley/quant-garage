#!/usr/bin/env python3
"""Validate every skill's requires.yml and required files.

Run from the repo root: python3 scripts/audit_requires.py
Needs PyYAML (pip install pyyaml). Exits 1 when any skill has an issue.
"""
import json
import sys
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

VALID_PRODUCTS = {
    "stocks", "options", "crypto", "forex", "indices", "futures",
    "benzinga_news", "benzinga_earnings", "benzinga_analyst_ratings",
    "benzinga_sentiment", "any",
}
VALID_TIERS = {"basic", "starter", "developer", "advanced", "business", "addon"}
VALID_INTERFACES = {"rest", "flat-files", "websocket"}
VALID_KINDS = {"foundation", "skill"}
VALID_OUTPUT_MODES = {
    "note", "stream", "table", "exception-report", "list", "dataset", "hybrid",
}


def audit_skill(skill_dir: Path) -> tuple[list[str], dict]:
    """Return (issues, parsed requires.yml). Parsed is empty when unreadable."""
    issues: list[str] = []
    requires_path = skill_dir / "requires.yml"
    if not requires_path.exists():
        return ["missing requires.yml"], {}
    if not (skill_dir / "SKILL.md").exists():
        issues.append("missing SKILL.md")
    try:
        doc = yaml.safe_load(requires_path.read_text(encoding="utf8"))
    except yaml.YAMLError as err:
        return [*issues, f"invalid YAML: {err}"], {}
    if not isinstance(doc, dict) or not doc.get("name"):
        return [*issues, "name missing"], {}
    if doc["name"] != skill_dir.name:
        issues.append(f'name "{doc["name"]}" doesn\'t match directory')
    if doc.get("kind") not in VALID_KINDS:
        issues.append(f"kind must be one of: {', '.join(sorted(VALID_KINDS))}")
    if doc.get("interface") not in VALID_INTERFACES:
        issues.append(f"interface must be one of: {', '.join(sorted(VALID_INTERFACES))}")
    if doc.get("kind") == "skill":
        if doc.get("output_mode") not in VALID_OUTPUT_MODES:
            issues.append(f"output_mode must be one of: {', '.join(sorted(VALID_OUTPUT_MODES))}")
        schema_path = skill_dir / "output-schema.json"
        if not schema_path.exists():
            issues.append("missing output-schema.json (required for user-facing skills)")
        else:
            try:
                json.loads(schema_path.read_text(encoding="utf8"))
            except json.JSONDecodeError as err:
                issues.append(f"output-schema.json is invalid JSON: {err}")
        if not (skill_dir / "references" / "rendering.md").exists():
            issues.append("missing references/rendering.md (required for user-facing skills)")
    requires = doc.get("requires")
    if not isinstance(requires, list) or not requires:
        issues.append("requires must be a non-empty list")
    else:
        for req in requires:
            if req.get("product") not in VALID_PRODUCTS:
                issues.append(f"invalid product: {req.get('product')}")
            if req.get("tier") not in VALID_TIERS:
                issues.append(f"invalid tier: {req.get('tier')}")
    return issues, doc


def main() -> int:
    skill_dirs = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())
    all_issues: list[tuple[str, str]] = []
    known: set[str] = set()
    fallbacks: list[tuple[str, str]] = []
    for skill_dir in skill_dirs:
        issues, doc = audit_skill(skill_dir)
        all_issues.extend((skill_dir.name, i) for i in issues)
        if doc.get("name"):
            known.add(doc["name"])
        fallbacks.extend((skill_dir.name, f) for f in doc.get("fallbacks") or [])
    for skill, fallback in fallbacks:
        if fallback not in known:
            print(f'WARN: {skill}: fallback "{fallback}" doesn\'t match any known skill')
    if not all_issues:
        print(f"OK: {len(skill_dirs)} skills, no issues found.")
        return 0
    print(f"\nFAIL: {len(all_issues)} issues found:\n", file=sys.stderr)
    for skill, problem in all_issues:
        print(f"  {skill}: {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
