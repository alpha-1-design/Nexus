"""Project initializer — sets up Nexus in a project directory."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ImportError:  # Python 3.10 — fall back to regex parsing
    tomllib = None


class ProjectInitializer:
    """Initialize Nexus in a project directory.

    Creates .nexus/config.json, scans the codebase for languages and
    frameworks, and generates an initial configuration tailored to the
    project.
    """

    def initialize(
        self,
        path: str = ".",
        force: bool = False,
        scan: bool = True,
    ) -> dict:
        """Initialize Nexus in the given project directory."""
        project_dir = Path(path).resolve()
        if not project_dir.exists():
            return {
                "status": "error",
                "message": f"Directory does not exist: {project_dir}",
            }

        nexus_dir = project_dir / ".nexus"
        config_file = nexus_dir / "config.json"

        if config_file.exists() and not force:
            return {
                "status": "error",
                "message": f"Nexus already initialized at {project_dir}",
                "details": {
                    "config": str(config_file),
                    "hint": "Use --force to overwrite",
                },
            }

        # Create .nexus directory
        nexus_dir.mkdir(parents=True, exist_ok=True)

        # Detect project type if scanning
        project_info = {}
        if scan:
            project_info = self._scan_project(project_dir)

        # Generate config
        config = self._generate_config(project_dir, project_info)

        # Write config
        config_file.write_text(json.dumps(config, indent=2, default=str))

        # Create .gitignore entry
        self._ensure_gitignore(project_dir)

        # Write .nexusignore defaults
        self._ensure_nexusignore(nexus_dir)

        # Create initial session index
        sessions_dir = nexus_dir / "sessions"
        sessions_dir.mkdir(exist_ok=True)

        result = {
            "status": "success",
            "message": f"Nexus initialized in {project_dir}",
            "details": {
                "config": str(config_file),
                "project_type": project_info.get("type", "unknown"),
                "languages": ", ".join(project_info.get("languages", [])),
                "frameworks": ", ".join(project_info.get("frameworks", [])),
                "files_scanned": project_info.get("file_count", 0),
            },
        }

        if project_info.get("warnings"):
            result["warnings"] = project_info["warnings"]

        return result

    def _scan_project(self, project_dir: Path) -> dict:
        """Scan a project directory for languages, frameworks, and structure."""
        info: dict = {
            "languages": [],
            "frameworks": [],
            "type": "unknown",
            "file_count": 0,
            "warnings": [],
        }

        # Detect common project files
        markers = {
            "pyproject.toml": ("python", "Python Project"),
            "package.json": ("javascript", "Node.js Project"),
            "Cargo.toml": ("rust", "Rust Project"),
            "go.mod": ("go", "Go Project"),
            "Gemfile": ("ruby", "Ruby Project"),
            "Makefile": ("make", "Makefile Project"),
            "CMakeLists.txt": ("cmake", "CMake Project"),
            "composer.json": ("php", "PHP Project"),
            "pom.xml": ("java", "Maven Project"),
            "build.gradle": ("java", "Gradle Project"),
            "mix.exs": ("elixir", "Elixir Project"),
            "rebar.config": ("erlang", "Erlang Project"),
            "stack.yaml": ("haskell", "Haskell Project"),
            "cabal.project": ("haskell", "Haskell Project"),
            "project.clj": ("clojure", "Clojure Project"),
            "dune-project": ("ocaml", "OCaml Project"),
            "pubspec.yaml": ("dart", "Dart Project"),
            "deno.json": ("typescript", "Deno Project"),
            "deno.jsonc": ("typescript", "Deno Project"),
            "bun.lockb": ("javascript", "Bun Project"),
        }

        for marker, (lang, project_type) in markers.items():
            if (project_dir / marker).exists():
                info["languages"].append(lang)
                if project_type not in info["frameworks"]:
                    info["frameworks"].append(project_type)
                info["type"] = project_type

        # Detect frameworks from package.json
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                framework_map = {
                    "react": "React", "next": "Next.js", "vue": "Vue.js",
                    "svelte": "Svelte", "angular": "Angular", "astro": "Astro",
                    "remix": "Remix", "gatsby": "Gatsby", "nuxt": "Nuxt",
                    "express": "Express", "fastify": "Fastify", "hono": "Hono",
                    "trpc": "tRPC", "prisma": "Prisma", "drizzle": "Drizzle",
                    "tailwindcss": "Tailwind CSS", "shadcn": "shadcn/ui",
                    "vite": "Vite", "webpack": "Webpack", "esbuild": "esbuild",
                }
                for key, framework in framework_map.items():
                    if key in deps and framework not in info["frameworks"]:
                        info["frameworks"].append(framework)
                if not info["languages"]:
                    info["languages"].append("javascript")
                if info["type"] == "unknown":
                    info["type"] = "Node.js Project"
            except (json.JSONDecodeError, KeyError):
                pass

        # Detect frameworks from pyproject.toml
        pyproject = project_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text()
                py_frameworks = {
                    "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
                    "starlette": "Starlette", "sqlalchemy": "SQLAlchemy",
                    "pydantic": "Pydantic", "pytest": "pytest",
                    "tortoise-orm": "Tortoise ORM", "celery": "Celery",
                }
                for key, fw in py_frameworks.items():
                    if key in content.lower() and fw not in info["frameworks"]:
                        info["frameworks"].append(fw)
                if "python" not in info["languages"]:
                    info["languages"].append("python")
            except Exception:
                pass

        # Detect languages from source files
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".jsx": "javascript", ".rs": "rust",
            ".go": "go", ".rb": "ruby", ".php": "php", ".java": "java",
            ".swift": "swift", ".kt": "kotlin", ".scala": "scala",
            ".ex": "elixir", ".exs": "elixir", ".hs": "haskell",
        }

        lang_counts: dict[str, int] = {}
        file_count = 0
        for root, _dirs, files in os.walk(project_dir):
            # Skip hidden dirs and node_modules
            if "/." in root or "node_modules" in root or "__pycache__" in root:
                continue
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in ext_map:
                    lang = ext_map[ext]
                    lang_counts[lang] = lang_counts.get(lang, 0) + 1
                    file_count += 1

        # Merge detected languages (prefer marker-detected over file-scanned)
        for lang, _count in sorted(lang_counts.items(), key=lambda x: -x[1]):
            if lang not in info["languages"]:
                info["languages"].append(lang)

        info["file_count"] = file_count

        # Determine project type
        if info["type"] == "unknown":
            if info["languages"]:
                info["type"] = f"{info['languages'][0].capitalize()} Project"
            else:
                info["type"] = "unknown"
                info["warnings"].append(
                    "Could not detect project type. "
                    "Nexus will work but may not have optimal context."
                )

        return info

    def _generate_config(self, project_dir: Path, project_info: dict) -> dict:
        """Generate initial configuration for a project."""
        config = {
            "version": "2.0",
            "project": {
                "name": project_dir.name,
                "type": project_info.get("type", "unknown"),
                "languages": project_info.get("languages", []),
                "frameworks": project_info.get("frameworks", []),
                "root": str(project_dir),
            },
            "settings": {
                "include": ["**/*"],
                "exclude": [
                    ".nexus/",
                    "node_modules/",
                    "__pycache__/",
                    ".git/",
                    "dist/",
                    "build/",
                    ".venv/",
                    "venv/",
                    ".env",
                    "*.pyc",
                ],
                "max_file_size_kb": 500,
                "max_files": 1000,
            },
            "skills": {
                "auto_load": True,
                "auto_activate": True,
                "tag_filters": [],
            },
        }

        # Add framework-specific config hints
        project_type = project_info.get("type", "")
        if "Python" in project_type:
            config["settings"]["exclude"].extend(
                [".mypy_cache/", ".pytest_cache/", "*.egg-info/"]
            )
        if "Node" in project_type:
            config["settings"]["exclude"].extend(
                ["bower_components/", ".next/", ".nuxt/"]
            )
        if "Rust" in project_type:
            config["settings"]["exclude"].append("target/")

        return config

    def _ensure_gitignore(self, project_dir: Path) -> None:
        """Ensure .gitignore has .nexus/ entry."""
        gitignore = project_dir / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            if ".nexus/" not in content:
                with open(gitignore, "a") as f:
                    f.write("\n# Nexus\n.nexus/\n")
        else:
            gitignore.write_text("# Nexus\n.nexus/\n")

    def _ensure_nexusignore(self, nexus_dir: Path) -> None:
        """Create default .nexusignore if it doesn't exist."""
        nexusignore = nexus_dir / ".nexusignore"
        if not nexusignore.exists():
            nexusignore.write_text(
                "# Files and patterns Nexus should completely ignore\n"
                ".git/\nnode_modules/\n__pycache__/\n*.pyc\n.venv/\n"
                "venv/\n.env\ndist/\nbuild/\n*.egg-info/\n"
            )


# ---------------------------------------------------------------------------
# /init — project context scanning + NEXUS.md generation
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".nexus", ".next", "target", "vendor",
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip anything larger than 2MB
MAX_WALK_DEPTH = 6

# Code file extensions -> language name (docs/config excluded on purpose).
LANG_EXTS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".rs": "Rust", ".go": "Go", ".rb": "Ruby",
    ".php": "PHP", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".scala": "Scala", ".ex": "Elixir", ".exs": "Elixir",
    ".hs": "Haskell", ".lua": "Lua", ".sh": "Shell", ".bash": "Shell",
    ".dart": "Dart", ".zig": "Zig", ".vue": "Vue", ".svelte": "Svelte",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".icns", ".tiff",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".flac", ".ogg", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".obj",
    ".pyc", ".pyo", ".class", ".wasm", ".ttf", ".otf", ".woff", ".woff2",
    ".eot", ".sqlite", ".db", ".bin", ".lockb", ".parquet", ".arrow",
}

AGENT_DOC_FILES = ["NEXUS.md", "CLAUDE.md", "AGENTS.md", ".cursorrules"]

FORMATTER_CONFIGS = {
    ".editorconfig": "editorconfig",
    "ruff.toml": "ruff",
    ".ruff.toml": "ruff",
    ".prettierrc": "prettier",
    "prettier.config.js": "prettier",
    ".eslintrc": "eslint",
    "eslint.config.js": "eslint",
    "setup.cfg": "setup.cfg",
    "mypy.ini": "mypy",
    ".stylelintrc": "stylelint",
    "rustfmt.toml": "rustfmt",
    ".gofmt": "gofmt",
}

SECRETS_FILES = [".env", ".env.local", ".env.production", "credentials", "secrets.yaml", ".npmrc"]

CONTEXT_CAP = 4000  # max NEXUS.md chars injected into system prompts


@dataclass
class InitReport:
    """Result of `init_project_context` — rendered as a card by REPL/CLI."""

    project_name: str
    languages: list[tuple[str, int]] = field(default_factory=list)  # top-5 (name, files)
    commands: list[str] = field(default_factory=list)              # detected build/test commands
    entry_points: list[str] = field(default_factory=list)          # pyproject [project.scripts]
    manifest_count: int = 0
    tree_lines: list[str] = field(default_factory=list)
    generated_path: str = ""
    backed_up: bool = False
    existing_agent_docs: list[str] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    license_type: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None
    secrets_present: list[str] = field(default_factory=list)
    readme_summary: str = ""
    warnings: list[str] = field(default_factory=list)


def _run_git(args: list[str], cwd: Path) -> str | None:
    """Run a read-only git query. Returns None on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except Exception:
        pass
    return None


def _iter_project_files(workspace: Path):
    """Yield (path, ext) for source files, pruning junk dirs and binaries."""
    for root, dirs, files in os.walk(workspace):
        rel = Path(root).relative_to(workspace)
        depth = len(rel.parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        if depth >= MAX_WALK_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext in BINARY_EXTS or ext in SKIP_DIRS:
                continue
            fpath = Path(root) / name
            try:
                if fpath.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield fpath, ext


def _detect_languages(workspace: Path) -> list[tuple[str, int]]:
    """Count files per language, return top 5 by file count."""
    counts: dict[str, int] = {}
    for _fpath, ext in _iter_project_files(workspace):
        lang = LANG_EXTS.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]


def _repo_map(workspace: Path, max_lines: int = 40) -> list[str]:
    """Top-2-level directory tree; skipped dirs appear as `name/…`."""
    lines = [f"{workspace.resolve().name}/"]
    try:
        top = sorted(
            workspace.iterdir(),
            key=lambda p: (p.is_file(), p.name.lower()),
        )
    except OSError as e:
        return lines + [f"(unreadable: {e})"]

    budget = max_lines - 2
    for entry in top:
        if budget <= 0:
            lines.append("…")
            break
        if entry.is_file():
            lines.append(f"├── {entry.name}")
            budget -= 1
            continue
        if entry.name in SKIP_DIRS:
            lines.append(f"├── {entry.name}/…")
            budget -= 1
            continue
        lines.append(f"├── {entry.name}/")
        budget -= 1
        try:
            children = sorted(entry.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            continue
        shown = 0
        for child in children:
            if budget <= 0 or shown >= 6:
                lines.append(f"│   └── …")
                budget -= 1
                break
            if child.name in SKIP_DIRS:
                lines.append(f"│   ├── {child.name}/…")
            else:
                suffix = "/" if child.is_dir() else ""
                lines.append(f"│   ├── {child.name}{suffix}")
            shown += 1
            budget -= 1
    return lines


def _read_json(path: Path) -> dict | None:
    """Parse a JSON file, returning None instead of crashing."""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _try_toml(path: Path) -> dict | None:
    """Parse TOML via tomllib when available."""
    if tomllib is None:
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _detect_manifests(workspace: Path, report: InitReport) -> None:
    """Best-effort manifest parsing: names, scripts, commands."""
    def cmd(label: str, command: str) -> None:
        entry = f"{label}: `{command}`"
        if entry not in report.commands:
            report.commands.append(entry)

    # --- package.json ---
    pkg = workspace / "package.json"
    if pkg.exists():
        report.manifest_count += 1
        data = _read_json(pkg)
        if data is None:
            report.warnings.append("package.json exists but is not valid JSON")
        else:
            scripts = data.get("scripts") or {}
            for key, label in (("build", "Build"), ("test", "Test"), ("dev", "Dev"), ("start", "Run")):
                if scripts.get(key):
                    cmd(label, f"npm run {key}" if key != "start" else "npm start")

    # --- pyproject.toml ---
    pyproject = workspace / "pyproject.toml"
    if pyproject.exists():
        report.manifest_count += 1
        data = _try_toml(pyproject)
        if data is None:
            if tomllib is None:
                # Python 3.10: regex fallback for the [project.scripts] table.
                text = ""
                try:
                    text = pyproject.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
                section = re.search(
                    r"^\[project\.scripts\]\s*$((?:^[ \t]*.+$\n?)*)", text, re.MULTILINE
                )
                if section:
                    for line in section.group(1).splitlines():
                        m = re.match(r"\s*([\w.-]+)\s*=", line)
                        if m:
                            report.entry_points.append(m.group(1))
                dep_m = re.search(r"^dependencies\s*=\s*\[", text, re.MULTILINE)
                if dep_m:
                    report.warnings.append(
                        "pyproject.toml parsed with regex fallback (Python 3.10 has no tomllib)"
                    )
            else:
                report.warnings.append("pyproject.toml exists but could not be parsed as TOML")
        else:
            project = data.get("project") or {}
            if project.get("name"):
                report.project_name = str(project["name"])
            scripts = project.get("scripts") or {}
            report.entry_points.extend(str(k) for k in scripts)
            deps = project.get("dependencies") or []
            opt = project.get("optional-dependencies") or {}
            dep_total = len(deps) + sum(len(v) if isinstance(v, list) else 0 for v in opt.values())
            if "pytest" in str(deps) or "pytest" in str(opt):
                cmd("Test", "pytest")
            if dep_total:
                report.conventions.append(f"{dep_total} declared dependencies")

    # --- requirements.txt ---
    req = workspace / "requirements.txt"
    if req.exists():
        report.manifest_count += 1
        try:
            lines = [
                l.strip() for l in req.read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip() and not l.strip().startswith(("#", "-"))
            ]
            if lines:
                report.conventions.append(f"requirements.txt: {len(lines)} packages")
        except Exception as e:
            report.warnings.append(f"requirements.txt unreadable: {e}")

    # --- Cargo.toml ---
    cargo = workspace / "Cargo.toml"
    if cargo.exists():
        report.manifest_count += 1
        data = _try_toml(cargo)
        name = (data or {}).get("package", {}).get("name")
        if not name:
            m = re.search(r"^\s*name\s*=\s*[\"']([^\"']+)[\"']", cargo.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
            name = m.group(1) if m else None
        if name:
            report.conventions.append(f"Rust crate: {name}")
            cmd("Test", "cargo test")
            cmd("Build", "cargo build")

    # --- go.mod ---
    gomod = workspace / "go.mod"
    if gomod.exists():
        report.manifest_count += 1
        m = re.search(r"^module\s+(\S+)", gomod.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        if m:
            report.conventions.append(f"Go module: {m.group(1)}")
        cmd("Test", "go test ./...")
        cmd("Build", "go build ./...")

    # --- composer.json ---
    composer = workspace / "composer.json"
    if composer.exists():
        report.manifest_count += 1
        data = _read_json(composer)
        if data is None:
            report.warnings.append("composer.json exists but is not valid JSON")
        else:
            if data.get("name"):
                report.conventions.append(f"PHP package: {data['name']}")

    # --- pom.xml ---
    pom = workspace / "pom.xml"
    if pom.exists():
        report.manifest_count += 1
        m = re.search(r"<artifactId>([^<]+)</artifactId>", pom.read_text(encoding="utf-8", errors="replace"))
        if m:
            report.conventions.append(f"Maven artifact: {m.group(1)}")
        cmd("Test", "mvn test")

    # --- Gemfile ---
    if (workspace / "Gemfile").exists():
        report.manifest_count += 1
        report.conventions.append("Ruby project (Gemfile)")
        cmd("Test", "bundle exec rake")

    # --- pubspec.yaml (Dart) ---
    pubspec = workspace / "pubspec.yaml"
    if pubspec.exists():
        report.manifest_count += 1
        m = re.search(r"^name:\s*(\S+)", pubspec.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        if m:
            report.conventions.append(f"Dart package: {m.group(1)}")
        cmd("Test", "flutter test")


def _read_readme(workspace: Path, report: InitReport) -> None:
    """Extract first heading + first paragraph (~300 chars) from README."""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme = workspace / name
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                report.warnings.append(f"README unreadable: {e}")
                return
            heading = None
            paragraph = ""
            for line in text.splitlines():
                stripped = line.strip()
                if heading is None and stripped.startswith("#"):
                    heading = stripped.lstrip("#").strip()
                    continue
                if stripped and not stripped.startswith(("#", "```", "!", "[!", "<", "|", "-", "*")):
                    paragraph = stripped
                    break
            if paragraph:
                paragraph = paragraph[:300]
            parts = [p for p in (heading, paragraph) if p]
            report.readme_summary = " — ".join(parts)
            return


def _detect_conventions(workspace: Path, report: InitReport) -> None:
    """Formatter configs + license type, detected only."""
    for fname, label in FORMATTER_CONFIGS.items():
        if (workspace / fname).exists():
            report.conventions.append(f"{label} config ({fname})")
    for lic in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        lic_path = workspace / lic
        if lic_path.exists():
            try:
                head = lic_path.read_text(encoding="utf-8", errors="replace")[:400].lower()
            except Exception:
                head = ""
            for key, label in (
                ("mit license", "MIT"), ("apache license", "Apache-2.0"),
                ("gnu general public license", "GPL"), ("mozilla public license", "MPL"),
                ("bsd", "BSD"), ("isc license", "ISC"), ("unlicense", "Unlicense"),
            ):
                if key in head:
                    report.license_type = label
                    break
            if report.license_type is None:
                report.license_type = f"present ({lic})"
            return


def _detect_git(workspace: Path, report: InitReport) -> None:
    if _run_git(["rev-parse", "--is-inside-work-tree"], workspace) != "true":
        report.warnings.append("Not a git repository (no git context available)")
        return
    report.git_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], workspace)
    remote = _run_git(["remote", "get-url", "origin"], workspace)
    if remote:
        report.git_remote = remote


def init_project_context(workspace: Path) -> InitReport:
    """Scan `workspace` and (re)generate NEXUS.md at its root.

    Deterministic and LLM-free by design: every step is best-effort and
    never raises — problems are collected into InitReport.warnings.
    An existing NEXUS.md is rotated to NEXUS.md.bak (single level).
    """
    workspace = Path(workspace).resolve()
    report = InitReport(project_name=workspace.name)

    if not workspace.exists():
        report.warnings.append(f"Workspace does not exist: {workspace}")
        return report

    nexus_md = workspace / "NEXUS.md"
    if nexus_md.exists():
        try:
            os.replace(nexus_md, workspace / "NEXUS.md.bak")
            report.backed_up = True
        except OSError as e:
            report.warnings.append(f"Could not back up existing NEXUS.md: {e}")

    # Existing agent context docs (presence only).
    for doc in AGENT_DOC_FILES:
        if (workspace / doc).exists() and doc not in report.existing_agent_docs:
            report.existing_agent_docs.append(doc)

    # README summary.
    _read_readme(workspace, report)

    # Languages + repo map.
    try:
        report.languages = _detect_languages(workspace)
    except Exception as e:
        report.warnings.append(f"Language scan failed: {e}")
    try:
        report.tree_lines = _repo_map(workspace)
    except Exception as e:
        report.warnings.append(f"Repo map failed: {e}")

    # Manifests.
    try:
        _detect_manifests(workspace, report)
    except Exception as e:
        report.warnings.append(f"Manifest scan failed: {e}")

    # Conventions, git, secrets.
    try:
        _detect_conventions(workspace, report)
    except Exception as e:
        report.warnings.append(f"Convention scan failed: {e}")
    try:
        _detect_git(workspace, report)
    except Exception as e:
        report.warnings.append(f"Git scan failed: {e}")
    for secret in SECRETS_FILES:
        if (workspace / secret).exists():
            report.secrets_present.append(secret)

    # If manifests didn't set a better name, prefer a README heading.
    if report.readme_summary and report.project_name == workspace.name:
        heading = report.readme_summary.split(" — ")[0]
        if heading:
            report.project_name = heading if len(heading) <= 60 else workspace.name

    content = _render_nexus_md(report)
    try:
        _atomic_write(nexus_md, content)
        report.generated_path = str(nexus_md)
    except OSError as e:
        report.warnings.append(f"Failed to write NEXUS.md: {e}")
    return report


def _atomic_write(path: Path, content: str) -> None:
    """Write via temp file + os.replace so NEXUS.md is never half-written."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".NEXUS.md.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _render_nexus_md(report: InitReport) -> str:
    """Build the NEXUS.md document from the scan report."""
    lang_str = ", ".join(f"{name} ({count} files)" for name, count in report.languages)
    lines: list[str] = []
    lines.append(f"# {report.project_name} — Nexus Context")
    lines.append("")
    lines.append(f"> Generated by `nexus init` on {date.today().isoformat()}. Re-run `/init` to refresh.")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    if report.readme_summary:
        lines.append(report.readme_summary)
    else:
        lines.append(f"Project rooted at `{report.project_name}/`. No README found — this section is minimal.")
    if lang_str:
        lines.append("")
        lines.append(f"**Languages (by file count):** {lang_str}.")
    lines.append("")
    lines.append("## Build & Test Commands")
    lines.append("")
    lines.append("> Detected from project manifests — verify before relying on them.")
    lines.append("")
    if report.commands:
        for c in report.commands:
            lines.append(f"- {c}")
    else:
        lines.append("- None detected from manifests.")
    if report.entry_points:
        lines.append(f"- Console entry points (pyproject `[project.scripts]`): {', '.join(report.entry_points)}")
    lines.append("")
    lines.append("## Repository Map")
    lines.append("")
    lines.append("```")
    lines.extend(report.tree_lines or ["(empty)"])
    lines.append("```")
    lines.append("")
    lines.append("## Conventions")
    lines.append("")
    if report.conventions:
        for c in report.conventions:
            lines.append(f"- {c}")
    else:
        lines.append("- No formatter configs detected.")
    if report.license_type:
        lines.append(f"- License: {report.license_type}")
    lines.append("")
    lines.append("## Notes for Nexus Agents")
    lines.append("")
    if report.git_branch:
        branch_note = f"- Git: on branch `{report.git_branch}`"
        if report.git_remote:
            branch_note += f", remote origin `{report.git_remote}`"
        lines.append(branch_note)
    else:
        lines.append("- Git: not a repository (or git unavailable).")
    if report.secrets_present:
        lines.append(
            f"- Secrets files present (presence only, never read): {', '.join(report.secrets_present)}"
        )
    else:
        lines.append("- No secrets files (.env etc.) detected at the project root.")
    if report.existing_agent_docs:
        lines.append(f"- Existing agent context files: {', '.join(report.existing_agent_docs)}")
    if report.warnings:
        lines.append("- Scan warnings: " + "; ".join(report.warnings))
    lines.append("")
    return "\n".join(lines)


def load_nexus_md(workspace: Path, cap: int = CONTEXT_CAP) -> str | None:
    """Read NEXUS.md from `workspace`, capped for system-prompt injection.

    Returns None when the file is missing or unreadable. Oversized files are
    truncated with an explicit note so the model knows context was cut.
    """
    try:
        path = Path(workspace) / "NEXUS.md"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > cap:
            text = text[:cap] + "\n\n[... NEXUS.md truncated to fit context — full file on disk ...]"
        return text
    except Exception:
        return None


def render_init_report(report: InitReport) -> str:
    """Render an InitReport as a REPL-style card (▸ bullets, dim details)."""
    cyan, green, yellow, dim, bold, reset = (
        "\033[36m", "\033[92m", "\033[93m", "\033[90m", "\033[1m", "\033[0m",
    )
    out = [""]
    if report.backed_up:
        out.append(f"  {yellow}▸{reset} Existing NEXUS.md backed up to {dim}NEXUS.md.bak{reset}")
    out.append(f"  {cyan}▸{reset} {bold}Project:{reset} {report.project_name}")
    if report.languages:
        langs = ", ".join(f"{n} {dim}({c}){reset}" for n, c in report.languages)
        out.append(f"  {cyan}▸{reset} {bold}Languages:{reset} {langs}")
    else:
        out.append(f"  {cyan}▸{reset} {bold}Languages:{reset} {dim}none detected{reset}")
    if report.commands or report.entry_points:
        out.append(f"  {cyan}▸{reset} {bold}Commands:{reset} {dim}detected — verify{reset}")
        for c in report.commands:
            out.append(f"      {dim}- {c}{reset}")
        if report.entry_points:
            out.append(f"      {dim}- entry points: {', '.join(report.entry_points)}{reset}")
    else:
        out.append(f"  {cyan}▸{reset} {bold}Commands:{reset} {dim}none detected{reset}")
    out.append(
        f"  {cyan}▸{reset} {bold}Manifests:{reset} {report.manifest_count} {dim}| repo map: "
        f"{len(report.tree_lines)} lines{reset}"
    )
    git_bits = report.git_branch or "no git"
    if report.git_remote:
        git_bits += f" {dim}→ {report.git_remote}{reset}"
    out.append(f"  {cyan}▸{reset} {bold}Git:{reset} {git_bits}")
    for doc in report.existing_agent_docs:
        out.append(f"  {yellow}▸{reset} Existing agent context file detected: {dim}{doc}{reset}")
    for s in report.secrets_present:
        out.append(f"  {dim}▸ Secrets file present (not read): {s}{reset}")
    for w in report.warnings:
        out.append(f"  {yellow}▸ Warning:{reset} {dim}{w}{reset}")
    if report.generated_path:
        out.append(f"  {green}▸{reset} Wrote {bold}{report.generated_path}{reset}")
    else:
        out.append(f"  {yellow}▸{reset} {bold}NEXUS.md not written{reset} {dim}(see warnings){reset}")
    out.append(f"  {dim}▸ NEXUS.md is now injected as project context on every turn{reset}")
    out.append("")
    return "\n".join(out)
