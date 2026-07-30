"""One-off script: aggregate claude-code-templates' MCP server definitions
into a single catalog JSON used by `nexus mcp catalog/search/install`.

Usage:
    python scripts/build_mcp_catalog.py [path-to-claude-code-templates-checkout]
"""
import json, pathlib, sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT.parent / "claude-code-templates"
SRC = SRC / "cli-tool" / "components" / "mcps"
OUT = REPO_ROOT / "nexus" / "data" / "mcp_catalog.json"

catalog = {}
skipped = []
for path in sorted(SRC.rglob("*.json")):
    category = path.relative_to(SRC).parts[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        skipped.append((str(path), str(e)))
        continue

    servers = data.get("mcpServers", {})
    for name, cfg in servers.items():
        entry = {
            "category": category,
            "description": cfg.get("description", ""),
            "transport": "sse" if "url" in cfg else "stdio",
        }
        if "url" in cfg:
            entry["url"] = cfg["url"]
        else:
            entry["command"] = cfg.get("command", "")
            entry["args"] = cfg.get("args", [])
        if cfg.get("env"):
            # keep keys only (values are usually placeholders / secrets)
            entry["env_vars"] = list(cfg["env"].keys())
        # avoid collisions: prefix with source file stem if name already used
        key = name
        if key in catalog and catalog[key] != entry:
            key = f"{name}-{path.stem}"
        catalog[key] = entry

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
print(f"Wrote {len(catalog)} MCP servers to {OUT}")
print(f"Skipped {len(skipped)}: {skipped[:3]}")
