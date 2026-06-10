"""Project initializer — sets up Nexus in a project directory."""

import json
import os
import subprocess
from pathlib import Path


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
        for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
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
