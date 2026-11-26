"""仓库内容扫描器：README / 依赖 / 启动脚本 / Windows 脚本 / 硬编码路径 / 密钥敏感信息。

所有发现带 confidence（high/medium/low）与 reason，便于人工复核。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target",
    "__pycache__", ".venv", "venv", ".idea", ".vscode", ".tox", ".mypy_cache",
    ".next", ".nuxt", "coverage", ".cache", "bower_components",
    # 测试夹具：几乎全是假密钥 / 假路径，扫描只增噪音
    "testdata", "test-data", "test_data", "testfixtures",
}
SKIP_EXT_LOW = {
    ".min.js", ".min.css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".gz", ".tar",
    ".tgz", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".jar", ".lock",
    ".mp4", ".mp3", ".wav", ".db", ".sqlite", ".bin",
}
MAX_FILE_BYTES = 1024 * 1024  # 1MB
MAX_FILES = 100_000

README_NAMES = {
    "readme.md", "readme.txt", "readme.rst", "readme.markdown",
    "readme", "readme.html", "readme.mdx",
}

DEP_MANIFESTS = {
    "package.json": "npm",
    "requirements.txt": "pip",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "pipenv",
    "go.mod": "go",
    "Cargo.toml": "cargo",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "Gemfile": "bundler",
    "composer.json": "composer",
    "pubspec.yaml": "flutter",
    "environment.yml": "conda",
}

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
    "pubspec.lock", "gradle.lockfile",
}

STARTUP_NAMES = {
    "makefile", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml", "justfile", "procfile", "taskfile.yml",
}
STARTUP_EXT = {".sh", ".bash", ".zsh", ".fish", ".mk"}

ENTRY_CANDIDATES = {
    "main.py", "app.py", "manage.py", "index.js", "index.ts", "index.jsx",
    "index.tsx", "main.go", "main.rs", "main.c", "main.cpp", "Main.java",
    "src/main.py", "src/main.rs", "src/main.go", "src/index.js", "src/index.ts",
    "src/index.jsx", "src/index.tsx", "src/App.js", "src/App.tsx", "cmd/main.go",
}
_ENTRY_RE = re.compile(r"(^|/)cmd/[^/]+/main\.go$|^src/main\.[A-Za-z]+$")

WINDOWS_EXT = {".bat", ".cmd", ".ps1", ".vbs"}

SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".java", ".kt",
    ".scala", ".go", ".rs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".lua", ".dart", ".ex",
    ".exs", ".hs", ".ml", ".clj", ".vue", ".svelte",
}
DOC_EXTS = {".md", ".rst", ".txt", ".markdown", ".mdx", ".adoc"}
DATA_EXTS = {
    ".csv", ".tsv", ".parquet", ".feather", ".jsonl", ".npy", ".npz",
    ".h5", ".hdf5", ".pt", ".pth", ".onnx", ".safetensors", ".arrow",
}

TEXT_EXTS = SOURCE_EXTS | DOC_EXTS | {
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd", ".vbs",
    ".yml", ".yaml", ".json", ".jsonl", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".xsd", ".csproj", ".vbproj", ".sln", ".gradle.kts", ".mod",
    ".env", ".properties", ".gradle", ".dockerfile", ".make", ".mk",
    ".html", ".htm", ".css", ".scss", ".less", ".sql", ".gitignore",
    ".dockerignore", ".editorconfig", ".pem", ".key", ".pub", ".crt",
    ".cer", ".der", ".p12", ".ipynb", ".tf", ".tfvars", ".proto", ".graphql",
}

_KEY_LIKE_BASES = {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "id_rsa.pub", "id_ed25519.pub"}

_EXTENSIONLESS_TEXT = {
    "makefile", "dockerfile", "procfile", "justfile", "gemfile", "rakefile",
    "taskfile", ".gitignore", ".dockerignore", ".editorconfig", ".env",
    "readme", "license", "licence", "changelog", "contributing", "authors",
    "copying", "notice", "news", "history", "security",
}


def _is_text(relpath: str) -> bool:
    low = relpath.lower()
    base = os.path.basename(low)
    if base in _EXTENSIONLESS_TEXT or base in _KEY_LIKE_BASES:
        return True
    _, ext = os.path.splitext(low)
    return ext in TEXT_EXTS


def _is_skipped_ext(relpath: str) -> bool:
    low = relpath.lower()
    return any(low.endswith(e) for e in SKIP_EXT_LOW)


def _looks_like_pem(abspath: str) -> bool:
    """无扩展名文件：读头部判断是否是 PEM 私钥/证书。"""
    try:
        with open(abspath, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    return b"-----BEGIN" in head


def walk_files(root: str) -> tuple[list[tuple[str, str]], list[str], dict]:
    """遍历文件树。

    返回 (可扫描文本文件 [(rel, abspath)], 全部相对路径, 统计)。
    """
    text_files: list[tuple[str, str]] = []
    all_paths: list[str] = []
    skipped_large = 0
    skipped_ext = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            all_paths.append(rel)
            if len(all_paths) > MAX_FILES:
                continue
            if not _is_text(rel):
                # 无扩展名的 PEM 文件（如 deploy/keys/id_deploy）也要扫
                if not (not os.path.splitext(rel)[1] and _looks_like_pem(abspath)):
                    continue
            if _is_skipped_ext(rel):
                skipped_ext += 1
                continue
            try:
                if os.path.getsize(abspath) > MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
            except OSError:
                continue
            text_files.append((rel, abspath))
    stats = {
        "total_paths": len(all_paths),
        "scanned_files": len(text_files),
        "skipped_large": skipped_large,
        "skipped_ext": skipped_ext,
    }
    return text_files, all_paths, stats


def _read_lines(abspath: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    try:
        with open(abspath, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                lines.append((i, line.rstrip("\n")))
    except OSError:
        pass
    return lines


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _word_count(text: str) -> int:
    """ASCII 词数 + CJK 字符数（中文按字算）。"""
    ascii_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    cjk_chars = len(re.findall(r"[一-鿿]", text))
    return ascii_words + cjk_chars


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

_SECTION_KEYWORDS = {
    "install": ("install", "installation", "setup", "安装", "部署", "快速开始", "quick start", "getting started"),
    "usage": ("usage", "use", "example", "quickstart", "使用", "示例", "用法", "通过"),
    "license": ("license", "licence", "许可证", "授权"),
    "docs": ("documentation", "docs", "文档", "api"),
    "config": ("config", "configuration", "配置", "设置"),
}


def scan_readme(files: list[tuple[str, str]]) -> dict:
    found = None
    for rel, abspath in files:
        if os.path.basename(rel).lower() in README_NAMES:
            found = (rel, abspath)
            break
    if not found:
        return {
            "exists": False, "path": None, "sections": [], "headings": 0,
            "word_count": 0, "links": 0, "code_blocks": 0, "missing": ["README 文件缺失"],
        }

    rel, abspath = found
    try:
        content = Path(abspath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        content = ""

    headings = [m.group(2).strip().lower() for m in _HEADING_RE.finditer(content)]
    sections = []
    for sec, kws in _SECTION_KEYWORDS.items():
        if any(kw in h for h in headings for kw in kws):
            sections.append(sec)
    # 标题没覆盖时，退化为「正文中是否有明确写法的安装/使用说明」
    lower = content.lower()
    if "install" not in sections and any(k in lower for k in ("pip install", "npm install", "yarn add", "go install", "cargo install")):
        sections.append("install")
    if "usage" not in sections and ("```" in content and any(k in lower for k in ("usage", "example", "运行", "启动"))):
        sections.append("usage")

    word_count = _word_count(content)
    links = len(re.findall(r"\]\(([^)]+)\)", content))
    code_blocks = content.count("```") // 2

    missing = []
    if "install" not in sections:
        missing.append("缺少安装/快速开始说明")
    if "usage" not in sections:
        missing.append("缺少使用示例")
    if "license" not in sections:
        missing.append("缺少许可证说明")

    return {
        "exists": True,
        "path": rel,
        "sections": sections,
        "headings": len(headings),
        "word_count": word_count,
        "links": links,
        "code_blocks": code_blocks,
        "missing": missing,
    }


def scan_deps(files: list[tuple[str, str]], all_paths: list[str]) -> dict:
    manifests: list[dict] = []
    for rel, abspath in files:
        base = os.path.basename(rel)
        if base in DEP_MANIFESTS:
            manifests.append({
                "path": rel,
                "type": DEP_MANIFESTS[base],
                "count": _count_deps(abspath, base),
            })
        elif rel.lower().endswith((".csproj", ".fsproj", ".vbproj")):
            manifests.append({
                "path": rel,
                "type": "nuget",
                "count": _count_deps(abspath, "csproj"),
            })
    locks = [p for p in all_paths if os.path.basename(p) in LOCKFILES]
    return {"manifests": manifests, "count": len(manifests), "lockfiles": locks}


def _count_deps(abspath: str, base: str) -> int:
    try:
        content = Path(abspath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if base == "package.json":
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, AttributeError):
            return 0
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        return len(deps)
    if base == "requirements.txt":
        return len([l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")])
    if base == "pyproject.toml":
        return _count_section_lines(content, dep_section_keywords=("dependencies",))
    if base == "Cargo.toml":
        return _count_section_lines(content, dep_section_keywords=("dependencies",))
    if base == "go.mod":
        n = 0
        in_block = False
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("require ("):
                in_block = True
                continue
            if in_block and s == ")":
                in_block = False
                continue
            if in_block and s and not s.startswith("//"):
                n += 1
            elif s.startswith("require ") and "(" not in s:
                n += 1
        return n
    if base == "Gemfile":
        return len(re.findall(r"^\s*gem\s+['\"]", content, re.MULTILINE))
    if base == "composer.json":
        try:
            data = json.loads(content)
            return len(data.get("require") or {}) + len(data.get("require-dev") or {})
        except (json.JSONDecodeError, AttributeError):
            return 0
    if base == "pom.xml":
        return len(re.findall(r"<dependency>", content))
    if base == "csproj":
        return len(re.findall(r"<PackageReference\b", content))
    if base in ("build.gradle", "build.gradle.kts"):
        return len(re.findall(r"^\s*(implementation|api|compileOnly|runtimeOnly)\s*[('\"]", content, re.MULTILINE))
    if base == "pubspec.yaml":
        return _count_yaml_block(content, "dependencies")
    if base in ("Pipfile", "environment.yml", "setup.py"):
        return 1
    return 1


def _count_section_lines(content: str, dep_section_keywords: tuple[str, ...]) -> int:
    """TOML 风格：统计 [xxx.dependencies] 段内的非空非注释行。"""
    n = 0
    in_deps = False
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_deps = any(k in s for k in dep_section_keywords)
            continue
        if in_deps and s and not s.startswith("#"):
            n += 1
    return n


def _count_yaml_block(content: str, key: str) -> int:
    n = 0
    in_block = False
    base_indent = None
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if s.startswith(key + ":"):
            in_block = True
            base_indent = indent
            continue
        if in_block:
            if indent <= base_indent and not s.startswith("-"):
                in_block = False
                continue
            if indent > base_indent:
                n += 1
    return n


def scan_startup(files: list[tuple[str, str]], all_paths: list[str]) -> dict:
    scripts: list[dict] = []
    ci: list[str] = []
    entries: list[str] = []
    for rel, _abspath in files:
        base = os.path.basename(rel)
        low = base.lower()
        if low in STARTUP_NAMES or any(rel.lower().endswith(e) for e in STARTUP_EXT):
            scripts.append({"path": rel})
    for rel in all_paths:
        low = rel.lower()
        if low.startswith(".github/workflows/") or low in (
            ".gitlab-ci.yml", "azure-pipelines.yml", ".travis.yml", ".circleci/config.yml",
        ):
            ci.append(rel)
        if rel in ENTRY_CANDIDATES or _ENTRY_RE.search(rel):
            entries.append(rel)

    pkg_scripts: list[str] = []
    for rel, abspath in files:
        if os.path.basename(rel) == "package.json":
            try:
                data = json.loads(Path(abspath).read_text(encoding="utf-8", errors="replace"))
                pkg_scripts = list((data.get("scripts") or {}).keys())
            except (json.JSONDecodeError, OSError):
                pkg_scripts = []

    return {
        "scripts": scripts,
        "ci": ci,
        "entries": entries,
        "package_scripts": pkg_scripts,
        "has_script_evidence": bool(scripts or pkg_scripts or entries),
        "has_ci_only": bool(ci) and not (scripts or pkg_scripts or entries),
    }


def scan_windows_scripts(files: list[tuple[str, str]]) -> list[str]:
    return [rel for rel, _ in files if rel.lower().endswith(tuple(WINDOWS_EXT))]


# --- 硬编码路径 ---

_HARDCODED_PATTERNS = [
    (re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]+"), "Windows 绝对路径"),
    (re.compile(r"\b[A-Za-z]:/[^\s\"'<>|]+"), "Windows 盘符路径(正斜杠)"),
    (re.compile(r"/(Users|home)/[^\s\"'<>|]*"), "Unix 用户目录绝对路径"),
    (re.compile(r"/(usr/local|opt|var/www)/[^\s\"'<>|]*"), "系统绝对路径"),
]

_CONTAINER_PATH_PREFIXES = (
    "/app/", "/workspace/", "/data/", "/usr/src/app", "/tmp/", "/dev/",
    "/proc/", "/var/lib/", "/opt/app/", "/srv/",
)
_PATH_PLACEHOLDERS = (
    "\\path\\to", "/path/to", "/you/", "/user/", "/username/", "/yourname/",
    "<", ">", "%s", "$(", "{{", "%USERNAME%", "%USERPROFILE%",
)
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "<!--", ";", "::", "REM ", "rem ", "--")

_CONTAINER_CONFIG_FILES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml",
    "compose.yaml", ".gitpod.yml", ".gitpod.yaml", "devcontainer.json",
}
_REGEX_CHARS = set("[]()?|")


def _is_comment_line(line: str) -> bool:
    s = line.strip()
    return s.startswith(_COMMENT_PREFIXES)


def _is_windows_script(rel: str) -> bool:
    return rel.lower().endswith(tuple(WINDOWS_EXT))


def _scan_paths_lines(rel: str, lines: list[tuple[int, str]]) -> list[dict]:
    findings: list[dict] = []
    seen: set[str] = set()
    count = 0
    script = _is_windows_script(rel)
    for lineno, line in lines:
        if count >= 20:
            break
        if len(line) > MAX_LINE_LEN:
            continue
        for pat, label in _HARDCODED_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            matched = m.group(0)[:80]
            low = matched.lower()
            if any(low.startswith(p) or p in low for p in _CONTAINER_PATH_PREFIXES):
                continue
            if any(p in matched for p in _PATH_PLACEHOLDERS):
                continue
            # 正则片段（[a-z]、(\?i) 等）不是真实路径
            if any(c in matched for c in _REGEX_CHARS):
                continue
            # $VAR 变量展开不是硬编码
            if "$" in matched or "%" in matched:
                continue
            # 以转义序列结尾（"v:\n"、"b:\b"）的不是真实路径
            if matched.endswith(("\\n", "\\t", "\\r", "\\b")):
                continue
            # 形如 x:// 的 scheme，不是盘符路径
            if re.match(r"^[A-Za-z]://", matched):
                continue
            # 路径在 '<' / '>' 前截断时（C:\Users\<name>），视为占位符
            tail = line[m.end():m.end() + 1]
            if tail in ("<", ">"):
                continue
            if matched in seen:
                continue
            seen.add(matched)
            comment = _is_comment_line(line)
            container_cfg = (
                os.path.basename(rel).lower() in _CONTAINER_CONFIG_FILES
                or ".devcontainer" in rel.lower()
            )
            if container_cfg:
                severity = "low"
            elif script:
                severity = "high"
            elif comment:
                severity = "low"
            else:
                severity = "medium"
            findings.append({
                "file": rel,
                "line": lineno,
                "match": matched,
                "kind": label,
                "severity": severity,
                "reason": "容器/开发环境配置中的路径" if container_cfg else (
                    "注释中的路径" if comment else (
                        "脚本中的绝对路径，换机器会失效" if script else "代码中的绝对路径"
                    )
                ),
            })
            count += 1
            break
    return findings


def scan_hardcoded_paths(files: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    for rel, abspath in files:
        findings.extend(_scan_paths_lines(rel, _read_lines(abspath)))
    return findings


# --- 密钥 / 敏感信息 ---

_PROVIDER_PATTERNS = [
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    ("GitHub Fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("GitLab PAT", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic Key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI Project Key", re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI Key (sk-)", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("HuggingFace Token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("npm Token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    # AWS Access Key ID 使用 base32 字符集（A-Z 与 2-7），不含 0/1/8/9
    ("AWS Access Key", re.compile(r"\bAKIA[A-Z2-7]{16}\b")),
    ("AWS STS Key", re.compile(r"\bASIA[A-Z2-7]{16}\b")),
    ("Tencent Cloud Key", re.compile(r"\bAKID[0-9A-Za-z]{13,}\b")),
]
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_BEARER_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._\-]{16,})")
_GENERIC_CRED_RE = re.compile(
    r"(?i)([A-Za-z0-9_.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|token|credential)[A-Za-z0-9_.-]*+)\s*[=:]\s*['\"]([^'\"]{6,120})['\"]"
)
_CRED_KEYWORDS = ("pass", "secret", "key", "token", "credential", "pwd")
MAX_LINE_LEN = 2000  # 超长行（notebook/minified JSON）跳过正则，避免回溯爆炸

# 已知假密钥 / 占位（对齐 gitleaks / trufflehog / 本仓库 lint_skills 的 allowlist）
_FP_RE = re.compile(
    r"\.\.\.|EXAMPLE|wJalrXUtnFEMI|\$\{|\{\{|abc123|abcdefghij|fake|dummy|sample|placeholder|xxxx",
    re.IGNORECASE,
)

_COMMON_VALUES = {
    "application/json", "text/plain", "text/html", "utf-8", "utf8", "ascii",
    "bearer", "basic", "localhost", "127.0.0.1", "changeme", "change-me",
    "admin", "root", "null", "none", "true", "false", "default", "password",
    "secret", "token", "test", "sample", "demo",
}
_BAD_VALUE_CHARS = set(" /:@%()${}<>*?&=,;\\\t\n")

_EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".tpl")

_SKIP_FILES_FOR_SECRETS = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "go.sum", "composer.lock"}


def _looks_placeholder(val: str) -> bool:
    if "<" in val or ">" in val:
        return True
    if any(ord(c) > 0x7F for c in val):
        return True
    # 小写冒号命名空间（auth:refresh: / cache:user）是缓存 key 前缀
    if re.fullmatch(r"[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)+:?", val):
        return True
    low = val.lower()
    if low in _COMMON_VALUES:
        return True
    if any(k in low for k in ("your", "example", "placeholder", "xxx", "replace",
                              "123456", "todo", "yourkey", "bearer", "sample", "dummy")):
        return True
    # 值本身就是「password」「secret」「token」等词（含下划线组合，如 mysecret_private_key）
    if any(k in low for k in ("password", "passwd", "secret", "token",
                              "apikey", "api_key", "private_key", "privatekey")):
        return True
    return False


def _value_looks_random(val: str) -> tuple[bool, float]:
    """通用凭据值是否像真实密钥：无分隔符/空格、含数字、足够长度、够高熵。"""
    if any(c in _BAD_VALUE_CHARS for c in val):
        return False, 0.0
    if val.lower() in _COMMON_VALUES:
        return False, 0.0
    # 全大写标识符常量（WORKSPACE_MANAGE_API_KEYS）不是密钥
    if val.isupper():
        return False, 0.0
    # 真实密钥几乎都含数字；纯字母标识符（RequestSecurityToken）排除
    if not any(c.isdigit() for c in val):
        return False, 0.0
    ent = _entropy(val)
    if len(val) >= 20 and ent >= 3.5:
        return True, ent
    if len(val) >= 12 and ent >= 3.0:
        return True, ent
    return False, ent


_ENV_FILE_RE = re.compile(r"^\.env(\.[A-Za-z0-9_-]+)?$")


def _is_example_file(base: str) -> bool:
    low = base.lower()
    return any(low.endswith(s) or low == s.lstrip(".") for s in _EXAMPLE_SUFFIXES)


def _scan_secrets_lines(rel: str, lines: list[tuple[int, str]]) -> list[dict]:
    findings: list[dict] = []
    seen: set[str] = set()
    base = os.path.basename(rel)
    if base in _SKIP_FILES_FOR_SECRETS or _is_example_file(base):
        return findings
    # .env 本体入库（.env.example 等已跳过）
    if _ENV_FILE_RE.match(base) and base != ".env.example":
        findings.append({
            "file": rel, "line": 0, "match": base[:80],
            "kind": ".env 文件被提交", "confidence": "high",
            "reason": "真实 .env 被提交到仓库，应加入 .gitignore",
        })
        seen.add(base)

    has_pem_end = any("-----END" in l for _, l in lines)
    # 文件本身是密钥检测规则库（gitleaks/trufflehog 等）：里面的样例密钥不是泄露
    is_rule_file = "/rules/" in rel.replace("\\", "/") and (
        "/config/" in rel.replace("\\", "/") or "/generate/" in rel.replace("\\", "/")
    )
    rule_like = is_rule_file or any(
        "gitleaks:allow" in l or "regexp.MustCompile" in l or "re.compile" in l
        or "SecretType" in l or "trufflehog:ignore" in l
        or "GenerateSampleSecrets" in l or "regex = " in l
        for _, l in lines
    )
    for lineno, line in lines:
        if len(line) > MAX_LINE_LEN:
            continue
        low_l = line.lower()
        if "gitleaks:allow" in low_l or "pragma: allowlist secret" in low_l \
                or "trufflehog:ignore" in low_l or "nosecret" in low_l:
            continue
        hit = _match_line(line, rel, base, has_pem_end, lines)
        if not hit:
            continue
        key = hit[0]
        if key in seen:
            continue
        seen.add(key)
        match, kind, confidence, reason = hit
        if rule_like:
            confidence = "low"
            reason = "文件中含密钥检测规则定义，疑似样例数据"
        findings.append({
            "file": rel, "line": lineno, "match": match[:80],
            "kind": kind, "confidence": confidence, "reason": reason,
            "sample": rule_like,
        })
    return findings


def scan_secrets(files: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    for rel, abspath in files:
        findings.extend(_scan_secrets_lines(rel, _read_lines(abspath)))
    return findings


MAX_DETECT_BYTES = 256 * 1024  # 内容检测跳过 256KB 以上文件（压缩包/大 JSON 等）
MAX_NOTEBOOK_BYTES = 64 * 1024  # notebook 输出噪音大且体积大，只扫小的
MAX_DOC_SECRET_BYTES = 128 * 1024  # 超大文档（书籍/清单）跳过密钥扫描


def scan_content(files: list[tuple[str, str]]) -> tuple[list[dict], list[dict]]:
    """一次遍历同时完成密钥与路径检测，避免重复读文件。"""
    secrets: list[dict] = []
    paths: list[dict] = []
    for rel, abspath in files:
        try:
            size = os.path.getsize(abspath)
        except OSError:
            continue
        low_rel = rel.lower()
        is_notebook = low_rel.endswith(".ipynb")
        is_doc = low_rel.endswith((".md", ".rst", ".txt", ".markdown", ".mdx", ".adoc"))
        if size > (MAX_NOTEBOOK_BYTES if is_notebook else MAX_DETECT_BYTES):
            continue
        # 超大文档基本是书籍/清单，扫密钥意义不大
        if is_doc and size > MAX_DOC_SECRET_BYTES:
            continue
        lines = _read_lines(abspath)
        secrets.extend(_scan_secrets_lines(rel, lines))
        # 文档/notebook 里的路径只会被降级为「文档示例」，跳过以省时间
        if not is_doc and not is_notebook:
            paths.extend(_scan_paths_lines(rel, lines))
    return secrets, paths


def _match_line(line: str, rel: str, base: str, has_pem_end: bool, lines: list[tuple[int, str]]):
    """返回 (match, kind, confidence, reason) 或 None。"""
    comment = _is_comment_line(line)

    for kind, pat in _PROVIDER_PATTERNS:
        m = pat.search(line)
        if not m or _FP_RE.search(m.group(0)):
            continue
        # 低熵「密钥」（如 npm_000000…）不是真实凭据
        if _entropy(m.group(0)) < 2.8:
            continue
        conf = "low" if comment else "high"
        return m.group(0), kind, conf, f"匹配 {kind} 格式" + ("（位于注释行）" if comment else "")

    m = _PEM_RE.search(line)
    if m:
        placeholder_pem = "XXXX" in line or "..." in line or "<" in line
        if has_pem_end and not placeholder_pem:
            return m.group(0), "私钥 (PEM)", "low" if comment else "high", "完整 PEM 私钥块（含 END）"
        return None

    m = _JWT_RE.search(line)
    if m and re.search(r"(?i)(token|bearer|auth|jwt)", line):
        return m.group(0), "JWT Token", "medium", "JWT 字面量且同行含 token/bearer 上下文"

    m = _BEARER_RE.search(line)
    if m and not _looks_placeholder(m.group(1)):
        return m.group(0), "Bearer Token", "medium", "硬编码 Bearer Token"

    low_line = line.lower()
    if not any(k in low_line for k in _CRED_KEYWORDS):
        return None

    m = _GENERIC_CRED_RE.search(line)
    if m:
        val = m.group(2)
        if _FP_RE.search(m.group(0)) or _looks_placeholder(val):
            return None
        ok, ent = _value_looks_random(val)
        if not ok:
            return None
        conf = "medium" if ent >= 3.5 else "low"
        if comment:
            conf = "low"
        return m.group(0), "疑似明文凭据", conf, f"明文赋值，熵 {ent:.1f}"

    return None


# --- 其他信号 ---

_LICENSE_RE = re.compile(r"^(licen[cs]e|copying)(\.[A-Za-z]+)?$", re.IGNORECASE)


def scan_signals(all_paths: list[str], files: list[tuple[str, str]], deps: dict, startup: dict) -> dict:
    ext_counts: Counter = Counter()
    doc_files = 0
    notebooks = 0
    data_files = 0
    source_files = 0
    license_file = None
    for rel in all_paths:
        base = os.path.basename(rel)
        _, ext = os.path.splitext(base.lower())
        ext_counts[ext] += 1
        if ext in SOURCE_EXTS:
            source_files += 1
        elif ext in DOC_EXTS or base.lower() in ("readme", "license", "licence", "changelog"):
            doc_files += 1
        elif ext == ".ipynb":
            notebooks += 1
            source_files += 1
        elif ext in DATA_EXTS:
            data_files += 1
        if license_file is None and _LICENSE_RE.match(base):
            license_file = rel

    text_total = max(1, doc_files + source_files + notebooks)
    return {
        "total_files": len(all_paths),
        "source_files": source_files,
        "doc_files": doc_files,
        "doc_ratio": round(doc_files / text_total, 3),
        "notebooks": notebooks,
        "data_files": data_files,
        "manifests": deps["count"],
        "lockfiles": len(deps.get("lockfiles") or []),
        "license_file": license_file,
        "has_ci": bool(startup.get("ci")),
        "entry_candidates": startup.get("entries") or [],
        "ext_counts": dict(ext_counts.most_common(12)),
    }


def scan_repo(root: str, options: dict | None = None) -> dict:
    """扫描仓库。

    options（全部可选，默认开启）：
        archives: 是否扫压缩包；decode: 是否做 Base64/URL 解码；
        history: 历史 commit 数（0 关闭）；gitleaks: 是否合并 gitleaks；
        custom_rules: 是否应用 .repohealth.json。
    """
    opts = {
        "archives": True, "decode": True, "history": 0,
        "gitleaks": False, "custom_rules": True,
    }
    if options:
        opts.update(options)

    files, all_paths, stats = walk_files(root)
    readme = scan_readme(files)
    deps = scan_deps(files, all_paths)
    startup = scan_startup(files, all_paths)
    signals = scan_signals(all_paths, files, deps, startup)
    secrets, hardcoded = scan_content(files)

    ignored = 0
    config_error = ""
    if opts["custom_rules"]:
        from .customrules import apply_ignores, apply_rules, load_config
        cfg = load_config(root)
        config_error = cfg.get("error", "")
        if cfg["rules"]:
            secrets.extend(apply_rules(files, cfg["rules"]))
        if opts["archives"]:
            from .deepscan import scan_archives
            secrets.extend(scan_archives(all_paths, root))
        if opts["decode"]:
            from .deepscan import scan_encoded
            secrets.extend(scan_encoded(files))
        if cfg["ignore"]:
            secrets, ignored = apply_ignores(secrets, cfg["ignore"])
    elif opts["archives"] or opts["decode"]:
        from .deepscan import scan_archives, scan_encoded
        if opts["archives"]:
            secrets.extend(scan_archives(all_paths, root))
        if opts["decode"]:
            secrets.extend(scan_encoded(files))

    if opts["gitleaks"]:
        from .engines import merge_gitleaks, run_gitleaks
        gl = run_gitleaks(root)
        if gl:
            secrets = merge_gitleaks(secrets, gl)

    history_findings: list[dict] = []
    history_note = ""
    if opts["history"]:
        from .history import scan_history
        history_findings, history_note = scan_history(root, int(opts["history"]))

    return {
        "readme": readme,
        "deps": deps,
        "startup": startup,
        "windows_scripts": scan_windows_scripts(files),
        "hardcoded_paths": hardcoded,
        "secrets": secrets,
        "history": history_findings,
        "history_note": history_note,
        "ignored": ignored,
        "config_error": config_error,
        "signals": signals,
        "stats": stats,
        "file_count": stats["scanned_files"],
    }
