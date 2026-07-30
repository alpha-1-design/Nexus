"""One-off script: port claude-code-templates agent library into Nexus's
skill format (name/description/category/tools frontmatter + markdown body).

Usage:
    python scripts/port_claude_code_templates_agents.py [path-to-claude-code-templates-checkout]

If no path is given, defaults to a sibling checkout at ../claude-code-templates
relative to this repo. Regenerates nexus/data/community_skills/.
"""
import re
import sys
from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT.parent / "claude-code-templates"
SRC = SRC / "cli-tool" / "components" / "agents"
DST = REPO_ROOT / "nexus" / "data" / "community_skills"
DST.mkdir(parents=True, exist_ok=True)

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)

def clean_description(desc: str) -> str:
    """Strip the huge <example> blocks the Claude Code templates embed in
    description fields -- keep just the first sentence/summary so it stays
    prompt-budget friendly, matching how Nexus already uses descriptions."""
    if not desc:
        return ""
    # cut at first <example> tag
    idx = desc.find("<example>")
    if idx != -1:
        desc = desc[:idx]
    desc = desc.replace("\\n", " ").strip()
    # collapse whitespace
    desc = re.sub(r"\s+", " ", desc)
    if len(desc) > 400:
        desc = desc[:397].rsplit(" ", 1)[0] + "..."
    return desc

count = 0
skipped = 0
for path in SRC.rglob("*.md"):
    category = path.relative_to(SRC).parts[0]
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        skipped += 1
        continue

    m = FRONTMATTER_RE.match(content)
    if not m:
        skipped += 1
        continue
    raw_fm, body = m.groups()
    try:
        fm = yaml.safe_load(raw_fm) or {}
    except yaml.YAMLError:
        skipped += 1
        continue

    name = str(fm.get("name", path.stem)).strip()
    if not name:
        name = path.stem

    desc = clean_description(str(fm.get("description", "")))
    tools_raw = fm.get("tools", "")
    if isinstance(tools_raw, str):
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    elif isinstance(tools_raw, list):
        tools = [str(t).strip() for t in tools_raw]
    else:
        tools = []

    new_fm = {
        "name": name,
        "description": desc,
        "category": category,
        "tools": tools,
        "tags": [category, "community", "claude-code-templates"],
        "version": "1.0",
    }

    out_dir = DST / category
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.md"

    frontmatter_yaml = yaml.safe_dump(new_fm, sort_keys=False, allow_unicode=True, width=1000)
    out_path.write_text(f"---\n{frontmatter_yaml}---\n\n{body.strip()}\n", encoding="utf-8")
    count += 1

print(f"Ported {count} skills, skipped {skipped}")
