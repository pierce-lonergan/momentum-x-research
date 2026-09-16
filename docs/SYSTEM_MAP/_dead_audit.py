"""Dead-code / dead-reference auditor for src/.

Hunts for two classes of latent bugs Pierce flagged after the
TrailingStopManager incident (4 months of dead callbacks):

  (1) Comments and docstrings that reference function/method/class
      names that no longer exist in the codebase. A comment saying
      "calls TrailingStopManager.maybe_widen()" is a future-bug-magnet
      when maybe_widen() has been deleted -- the next reader assumes
      the comment is true and trusts the call site.

  (2) Source references (not in comments) to D-codes whose registry
      status is DEPRECATED or REVERTED. Code paths that still mention
      a reverted decision are usually stale machinery that needs
      either re-enablement or deletion.

USAGE:
  python docs/SYSTEM_MAP/_dead_audit.py                 # report only
  python docs/SYSTEM_MAP/_dead_audit.py --strict        # exit 1 on any finding
  python docs/SYSTEM_MAP/_dead_audit.py --include-tests # also scan tests/
  python docs/SYSTEM_MAP/_dead_audit.py --json          # machine-readable output

EXIT CODES:
  0 = no findings (or report mode)
  1 = findings present AND --strict was passed

This is a HEURISTIC. Expect false positives -- the script does not
do full static analysis. Triage rules:

  * "comment references undefined Foo.bar" -- check git log for bar;
    if deleted >30d ago, fix the comment OR restore the method.
  * "code references DEPRECATED D-code" -- check d_codes.md entry;
    if superseded_by exists, migrate; otherwise delete the dead path.

Maintained alongside _linter.py as part of the SYSTEM_MAP discipline
kit (see INDEX.md "Tools" section).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Windows console defaults to cp1252; force UTF-8 so docstring math
# symbols (gamma, eta, theta) don't crash the report.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).resolve().parents[2]
SM_DIR = Path(__file__).resolve().parent
SRC_DIR = REPO / "src"
TESTS_DIR = REPO / "tests"
SCRIPTS_DIR = REPO / "scripts"
D_CODES_FILE = SM_DIR / "d_codes.md"

# ── builtin / stdlib / third-party whitelist ───────────────────────
# Names that legitimately appear in comments without being defined in
# our source tree. Keep this minimal and explicit -- a too-permissive
# whitelist hides real bugs.

PYTHON_BUILTINS = {
    "abs", "all", "any", "ascii", "bin", "bool", "breakpoint", "bytearray",
    "bytes", "callable", "chr", "classmethod", "compile", "complex",
    "delattr", "dict", "dir", "divmod", "enumerate", "eval", "exec",
    "filter", "float", "format", "frozenset", "getattr", "globals",
    "hasattr", "hash", "help", "hex", "id", "input", "int", "isinstance",
    "issubclass", "iter", "len", "list", "locals", "map", "max", "memoryview",
    "min", "next", "object", "oct", "open", "ord", "pow", "print",
    "property", "range", "repr", "reversed", "round", "set", "setattr",
    "slice", "sorted", "staticmethod", "str", "sum", "super", "tuple",
    "type", "vars", "zip", "__import__",
    # exception names
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "RuntimeError", "AttributeError", "FileNotFoundError",
    "NotImplementedError", "StopIteration", "OSError", "TimeoutError",
    "ConnectionError", "AssertionError", "ImportError", "ModuleNotFoundError",
    "ZeroDivisionError", "OverflowError", "MemoryError", "RecursionError",
    "NameError", "UnicodeDecodeError", "UnicodeEncodeError",
    "BaseException", "GeneratorExit", "KeyboardInterrupt", "SystemExit",
    # common stdlib types/functions referenced informally
    "datetime", "date", "time", "timedelta", "timezone", "Path", "Decimal",
    "Enum", "Optional", "Union", "List", "Dict", "Tuple", "Set", "Any",
    "Iterable", "Iterator", "Callable", "Generator", "Mapping",
    "dataclass", "field", "asdict", "astuple",
    "defaultdict", "deque", "namedtuple", "OrderedDict", "Counter",
    "partial", "lru_cache", "reduce",
    "json", "re", "os", "sys", "io", "math", "logging", "asyncio",
    "threading", "subprocess", "uuid", "hashlib", "base64", "pickle",
    "copy", "itertools", "functools", "collections", "typing",
    "abc", "contextlib", "warnings", "traceback", "weakref",
    "concurrent", "queue", "socket", "ssl", "http", "urllib",
    "loads", "dumps", "load", "dump", "join", "split", "strip",
    "format", "encode", "decode", "items", "keys", "values",
    "append", "extend", "pop", "remove", "insert", "clear", "copy",
    "get", "setdefault", "update", "fromkeys", "count", "index",
    "startswith", "endswith", "replace", "lower", "upper", "title",
    "find", "rfind", "sub", "match", "search", "compile",
    "now", "utcnow", "fromtimestamp", "isoformat", "strftime", "strptime",
    "sleep", "monotonic", "perf_counter", "time_ns",
    "exists", "is_file", "is_dir", "mkdir", "rmdir", "unlink", "rename",
    "read_text", "write_text", "read_bytes", "write_bytes",
    "info", "warning", "error", "critical", "debug", "exception",
    "getLogger", "basicConfig", "FileHandler", "StreamHandler",
    # tk/qt/etc that show up in docstrings sometimes -- not expected here
    # third-party we use heavily
    "pd", "np", "pa", "pq", "DataFrame", "Series", "Index", "Timestamp",
    "Timedelta", "NaT", "NaN", "Categorical", "Period",
    "read_csv", "read_parquet", "to_csv", "to_parquet", "to_datetime",
    "concat", "merge", "groupby", "agg", "apply", "transform", "filter",
    "array", "ndarray", "zeros", "ones", "arange", "linspace",
    "alpaca", "AlpacaClient", "TradingClient", "StockHistoricalDataClient",
    "OrderRequest", "MarketOrderRequest", "LimitOrderRequest", "StopOrderRequest",
    "OrderSide", "OrderType", "OrderStatus", "OrderClass", "TimeInForce",
    "discord", "Embed", "Webhook", "post",
    "openai", "anthropic", "ChatCompletion", "Message", "completion",
    "requests", "Session", "Response", "get", "post", "put", "delete",
    "BeautifulSoup", "soup", "find_all", "select_one",
    "pytest", "fixture", "mark", "parametrize", "raises", "approx",
    "Mock", "MagicMock", "patch", "PropertyMock", "call",
    "tomllib", "toml", "yaml",
    # XGBoost / lightgbm / sklearn that appear in docstrings
    "XGBClassifier", "XGBRegressor", "LGBMClassifier", "LGBMRegressor",
    "CatBoostClassifier", "CatBoostRegressor", "LogisticRegression",
    "RandomForestClassifier", "StackingClassifier", "Pipeline",
    "StandardScaler", "fit", "predict", "predict_proba", "score",
    "transform", "fit_transform", "cross_val_score", "train_test_split",
    # Pydantic v2 surface that shows up in docstrings constantly
    "model_copy", "model_dump", "model_validate", "model_dump_json",
    "model_validate_json", "model_fields", "model_config", "BaseModel",
    "Field", "ConfigDict", "field_validator", "model_validator",
    # technical-indicator math notation in docstrings (Bollinger Bands(20,2))
    "Bands", "HalfNormal", "Normal", "Beta", "Gamma", "Bernoulli", "Poisson",
    "Bollinger", "Wilder", "Donchian", "Keltner", "Chaikin",
}

# Words that look like CamelCase identifiers but are common English
# prose tokens in our docstrings (TODOs, headers, prose section labels).
PROSE_WHITELIST = {
    "TODO", "FIXME", "XXX", "HACK", "NOTE", "WARNING", "DANGER", "DEPRECATED",
    "IMPORTANT", "NOTICE", "REVIEW", "BUG", "OPTIMIZE",
    "PASS", "FAIL", "OK", "ERROR", "SKIP", "DEBUG", "INFO", "CRITICAL",
    "AM", "PM", "ET", "UTC", "GMT", "EST", "EDT", "PST", "PDT",
    "API", "URL", "URI", "HTTP", "HTTPS", "JSON", "TOML", "YAML", "XML",
    "CSV", "TSV", "SQL", "REST", "GET", "POST", "PUT", "DELETE", "PATCH",
    "ID", "UUID", "CRC", "MD5", "SHA",
    "BUY", "SELL", "STOP", "LIMIT", "OTO", "OCO", "TIF", "DAY", "GTC", "IOC", "FOK",
    "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T0",
    "L1", "L2", "L3", "L4",
    "EOD", "SOD", "EOW", "EOM", "EOY", "Q1", "Q2", "Q3", "Q4",
    "P50", "P95", "P99", "P999",
    "USD", "AUM", "PNL", "P&L", "ROI", "ROIC",
    "EMA", "SMA", "VWAP", "MACD", "RSI", "ATR", "ADX", "OBV",
    "BUY", "SELL", "LONG", "SHORT", "FILL", "PARTIAL",
    "CI", "CD", "PR", "MR", "QA", "UAT",
    "OS", "CPU", "GPU", "RAM", "SSD", "HDD",
    "AND", "OR", "NOT", "XOR", "NAND", "NOR",
    "TRUE", "FALSE", "NULL", "NONE",
    "MFCS", "BOCPD", "LLM", "RAG", "KB", "FSM",
    "RUNBOOK", "INDEX", "README", "TODO", "DONE",
    "DEFAULT", "AUTO", "MANUAL", "FORCE", "DRY", "WET",
    "STDIN", "STDOUT", "STDERR",
    "SOTA", "MVP", "POC", "WIP", "TBD", "N/A",
}

# Module names common in third-party that may appear as bare references.
# These are checked as the FIRST component of a dotted reference.
THIRD_PARTY_MODULES = {
    "pd", "np", "pa", "pq", "plt", "sns", "tf", "torch",
    "alpaca", "AlpacaClient", "TradingClient", "StockHistoricalDataClient",
    "discord", "Discord", "Embed", "Webhook",
    "openai", "anthropic", "Anthropic", "OpenAI", "Groq", "Together",
    "requests", "httpx", "aiohttp",
    "pytest", "Mock", "MagicMock", "patch",
    "json", "re", "os", "sys", "io", "math", "logging", "asyncio",
    "threading", "subprocess", "uuid", "hashlib", "base64", "pickle",
    "copy", "itertools", "functools", "collections", "typing",
    "tomllib", "toml", "yaml",
    "datetime", "Path", "Decimal", "Enum",
    "xgboost", "xgb", "lightgbm", "lgb", "catboost", "cb",
    "sklearn", "skl", "scipy", "stats",
    "tabpfn", "TabPFNClassifier", "TabPFNRegressor",
    # asyncio surface we use in docstring examples constantly
    "create_task", "ensure_future", "gather", "wait", "shield",
    "run", "run_until_complete", "get_event_loop", "new_event_loop",
    "Lock", "Event", "Semaphore", "Queue", "Future", "Task",
    # httpx exception classes mentioned in docstrings
    "ReadTimeout", "HTTPStatusError", "ConnectError", "TimeoutException",
    "RequestError", "PoolTimeout", "NetworkError",
    # http.server attributes
    "HTTPServer", "allow_reuse_address", "BaseHTTPRequestHandler",
    # Polars / pandas method names that recur in docstrings
    "fill_null", "fill_nan", "drop_nulls", "with_columns", "select",
    "filter", "groupby", "agg", "apply", "map",
    # scipy.stats distribution names beyond what's already listed
    "StudentT", "T", "F", "ChiSquared", "Exponential", "LogNormal",
    # math-notation placeholders we'd use in docstring formulas
    "P_close", "P_open", "P_high", "P_low", "P_current",
}


# ── data classes ───────────────────────────────────────────────────


@dataclass
class Finding:
    kind: str          # "dead_comment_ref" | "dead_dcode_use" | "broken_import"
    file: str
    line: int
    target: str        # the reference that couldn't be resolved
    context: str       # the comment / line content
    note: str = ""     # extra context (e.g., "D-code DEPRECATED in 2025-09-12 commit")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "target": self.target,
            "context": self.context,
            "note": self.note,
        }


# ── collection: defined names in repo ──────────────────────────────


def collect_defined_names(roots: Iterable[Path]) -> tuple[set[str], set[str], set[str]]:
    """Walk all .py files under given roots; return (functions, classes, attributes).

    Functions: every `def` name (top-level + methods).
    Classes: every `class` name.
    Attributes: every dataclass/Pydantic field (AnnAssign inside ClassDef),
                every class-level Assign target, every `self.X = ...` inside
                __init__. Captures the second half of `Foo.bar` references
                that the AST function/class scan misses.
    """
    funcs: set[str] = set()
    classes: set[str] = set()
    attrs: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            files = [root] if root.suffix == ".py" else []
        else:
            files = list(root.rglob("*.py"))
        for path in files:
            try:
                src = path.read_text(encoding="utf-8")
                tree = ast.parse(src, filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    classes.add(node.name)
                    # Capture class-body attribute declarations.
                    for stmt in node.body:
                        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                            attrs.add(stmt.target.id)
                        elif isinstance(stmt, ast.Assign):
                            for t in stmt.targets:
                                if isinstance(t, ast.Name):
                                    attrs.add(t.id)
                        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            # capture `self.X = ...` inside method body
                            for sub in ast.walk(stmt):
                                if isinstance(sub, ast.Assign):
                                    for t in sub.targets:
                                        if (isinstance(t, ast.Attribute)
                                                and isinstance(t.value, ast.Name)
                                                and t.value.id == "self"):
                                            attrs.add(t.attr)
                                elif isinstance(sub, ast.AnnAssign):
                                    if (isinstance(sub.target, ast.Attribute)
                                            and isinstance(sub.target.value, ast.Name)
                                            and sub.target.value.id == "self"):
                                        attrs.add(sub.target.attr)
    return funcs, classes, attrs


def collect_imported_names(path: Path) -> set[str]:
    """Per-file: names brought in by `import` / `from ... import ...`.

    Used so we don't flag `requests.get` as a dead reference when the
    file imports `requests`.
    """
    names: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
            if node.module:
                names.add(node.module.split(".")[0])
    return names


# ── extraction: comments + docstrings ──────────────────────────────


# Capture `Word.method(` and `Word.method ` -- the dotted form.
# Word starts with letter, contains [A-Za-z0-9_].
DOTTED_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")

# File extensions that show up after a dotted token in path-shaped
# placeholders (DD.jsonl, foo.csv, YYYY.parquet). Suppress these --
# they're filenames, not code refs.
FILE_EXTENSIONS = {
    "py", "pyc", "pyo", "pyd", "pyi",
    "json", "jsonl", "yaml", "yml", "toml", "ini", "cfg", "env",
    "csv", "tsv", "parquet", "feather", "pkl", "pickle", "joblib", "npy", "npz",
    "txt", "md", "rst", "log", "lock", "sh", "ps1", "bat",
    "html", "htm", "xml", "svg", "png", "jpg", "jpeg", "gif", "pdf",
    "tar", "gz", "zip", "7z", "bz2", "xz",
    "dot", "gv", "dat", "db", "sqlite",
    # TLDs that appear in domain mentions inside comments (SEC.gov etc.)
    "gov", "com", "org", "io", "co", "net", "edu", "ai", "dev",
}

# Generic example function names that show up in docstring "how to use"
# blocks. Not real refs.
DOCSTRING_EXAMPLE_NAMES = {
    "fetch_data", "do_work", "do_thing", "do_something", "process_data",
    "my_func", "my_function", "example_func", "callback",
    "handle_event", "on_event", "on_message", "main",
}

# Capture bare `name(` -- function-call shape in comments. We require
# the parenthesis to filter out arbitrary prose nouns.
CALL_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\(")

# Comment line: `# ...` after optional whitespace, not inside a string.
# We rely on Python tokenize to get accurate comments (string-aware).
import tokenize
import io


def extract_comments_and_docstrings(path: Path) -> list[tuple[int, str]]:
    """Yield (line_number, text) for every comment and module/class/func docstring."""
    out: list[tuple[int, str]] = []
    try:
        src = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return out

    # Comments via tokenize
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenizeError, IndentationError, SyntaxError):
        pass

    # Docstrings via AST
    try:
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return out

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                # Approximate line: first statement line for module, otherwise body[0].lineno
                if isinstance(node, ast.Module):
                    ln = node.body[0].lineno if node.body else 1
                else:
                    ln = node.body[0].lineno if node.body else getattr(node, "lineno", 1)
                for off, line in enumerate(doc.splitlines()):
                    out.append((ln + off, line))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return out


# ── core check (1): dead comment references ────────────────────────


def check_dead_comment_refs(
    path: Path,
    defined: set[str],
    imported: set[str],
) -> list[Finding]:
    """Find references in comments/docstrings that don't resolve."""
    findings: list[Finding] = []
    safe = defined | imported | PYTHON_BUILTINS | THIRD_PARTY_MODULES
    comments = extract_comments_and_docstrings(path)
    rel = str(path.relative_to(REPO)).replace("\\", "/")

    for lineno, text in comments:
        # Skip URLs entirely -- they contain dots and parens but no code refs.
        if "http://" in text or "https://" in text:
            continue
        # Skip lines that are mostly a code block fence or shell command.
        stripped = text.lstrip("#").strip()
        if stripped.startswith(("$", ">", "python ", "pip ", "git ")):
            continue
        # Skip lines that look like literal Python source in a docstring
        # example block (def/async def at the start of the line is a strong
        # signal of "here's how to use this", not a comment about a callable).
        if stripped.startswith(("def ", "async def ", "class ", "import ", "from ")):
            continue
        # Skip lines that reference D-codes with subcode form (D310.noise);
        # the dotted-ref regex would otherwise see "D310" + "noise" as a
        # Class.method pattern.
        if DCODE_REF_RE.search(text):
            # Allow the line through but blank it so dotted-ref scan skips it.
            # Simpler: just continue (D-code refs aren't what this check is for).
            continue

        # Dotted refs: Foo.bar -- check `bar` (the method) is defined somewhere.
        # We DON'T check Foo because that's class-resolution; if Foo is in
        # PROSE_WHITELIST (e.g., "PASS.bar") skip outright.
        for match in DOTTED_REF_RE.finditer(text):
            obj, attr = match.group(1), match.group(2)
            if obj in PROSE_WHITELIST or attr in PROSE_WHITELIST:
                continue
            if attr.lower() in FILE_EXTENSIONS:
                continue  # path placeholder, not a code ref
            if obj in safe and attr in safe:
                continue
            if attr in safe:
                continue
            # Only flag if `attr` looks like a method name (lowercase first char)
            # and `obj` looks like a class name (uppercase first char) -- the
            # high-signal Foo.bar pattern Pierce flagged.
            if obj[0].isupper() and attr[0].islower() and len(attr) >= 3:
                findings.append(Finding(
                    kind="dead_comment_ref",
                    file=rel,
                    line=lineno,
                    target=f"{obj}.{attr}",
                    context=text.strip()[:200],
                ))

        # Bare call refs: bar() -- only if the name is reasonably distinctive
        # (>= 4 chars, snake_case, not in any whitelist).
        for match in CALL_REF_RE.finditer(text):
            name = match.group(1)
            if name in safe or name in PROSE_WHITELIST:
                continue
            if name in DOCSTRING_EXAMPLE_NAMES:
                continue
            if name.isupper():  # likely an acronym
                continue
            if len(name) < 5:   # too generic to flag without false positives
                continue
            if "_" not in name and name.islower():
                # camelCase/lowercase no underscore: too risky to flag
                continue
            if name.startswith("_"):  # private convention, often local
                continue
            # Class-shaped names (UpperCase): only flag if not in safe.
            # Plenty of legit doc references like `Path("x")` to constructors.
            if name[0].isupper() and name in safe:
                continue
            findings.append(Finding(
                kind="dead_comment_ref",
                file=rel,
                line=lineno,
                target=f"{name}()",
                context=text.strip()[:200],
            ))

    return findings


# ── core check (2): code references to DEPRECATED D-codes ──────────


def load_dcode_statuses() -> dict[str, dict]:
    """Parse d_codes.md and return {label: {status, superseded_by, ...}}."""
    if not D_CODES_FILE.exists():
        return {}
    text = D_CODES_FILE.read_text(encoding="utf-8")
    toml_blocks = re.findall(r"```toml\s*\n(.*?)\n```", text, re.DOTALL)
    if not toml_blocks:
        return {}
    combined = "\n\n".join(toml_blocks)
    try:
        data = tomllib.loads(combined)
    except tomllib.TOMLDecodeError:
        return {}
    out: dict[str, dict] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        if "label" not in value or "status" not in value:
            continue
        out[value["label"]] = value
    return out


DCODE_REF_RE = re.compile(r"\bD([0-9]{2,4})(?:\.([0-9a-z]+))?\b")


def check_deprecated_dcode_use(
    path: Path,
    dcode_map: dict[str, dict],
) -> list[Finding]:
    """Find references to DEPRECATED/REVERTED D-codes in source code."""
    findings: list[Finding] = []
    rel = str(path.relative_to(REPO)).replace("\\", "/")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        for match in DCODE_REF_RE.finditer(line):
            num = match.group(1)
            sub = match.group(2)
            label = f"D{num}" + (f".{sub}" if sub else "")
            # Try both with and without the subcode -- registry sometimes
            # has just D293 even when code says D293.7.
            entry = dcode_map.get(label) or dcode_map.get(f"D{num}")
            if not entry:
                continue
            status = entry.get("status", "").upper()
            if status not in {"DEPRECATED", "REVERTED", "DELETED"}:
                continue
            superseded = entry.get("superseded_by", "")
            note = f"status={status}"
            if superseded:
                note += f"; superseded_by={superseded}"
            findings.append(Finding(
                kind="dead_dcode_use",
                file=rel,
                line=i,
                target=label,
                context=line.strip()[:200],
                note=note,
            ))
    return findings


# ── core check (3): broken intra-repo imports ─────────────────────


def check_broken_imports(path: Path) -> list[Finding]:
    """Flag `from src.foo.bar import X` where `src/foo/bar.py` doesn't exist.

    Catches the selection_arena/__init__.py pattern -- modules removed
    from disk but still imported by their package's __init__. These crash
    at import time the moment anyone touches the package.
    """
    findings: list[Finding] = []
    rel = str(path.relative_to(REPO)).replace("\\", "/")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module:
            continue
        # Only check intra-repo imports (src.*, scripts.*, tests.*, config.*).
        top = node.module.split(".")[0]
        if top not in {"src", "scripts", "tests", "config"}:
            continue
        # Resolve the module path under REPO.
        parts = node.module.split(".")
        candidate_pkg = REPO.joinpath(*parts) / "__init__.py"
        candidate_mod = REPO.joinpath(*parts[:-1], parts[-1] + ".py") if parts else None
        if candidate_pkg.exists() or (candidate_mod and candidate_mod.exists()):
            continue
        # Neither package nor module found -- broken.
        names = ", ".join(a.name for a in node.names)
        findings.append(Finding(
            kind="broken_import",
            file=rel,
            line=node.lineno,
            target=node.module,
            context=f"from {node.module} import {names}",
            note="module not found on disk",
        ))
    return findings


# ── runner ──────────────────────────────────────────────────────────


def iter_py_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix == ".py":
                yield root
        else:
            yield from sorted(root.rglob("*.py"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any finding is reported")
    parser.add_argument("--include-tests", action="store_true",
                        help="also scan tests/ (off by default; high FP rate)")
    parser.add_argument("--json", action="store_true",
                        help="emit findings as JSON to stdout")
    parser.add_argument("--max-per-file", type=int, default=5,
                        help="cap findings per file in human output (default 5)")
    args = parser.parse_args()

    roots: list[Path] = [SRC_DIR, REPO / "config", REPO / "main.py", SCRIPTS_DIR]
    if args.include_tests:
        roots.append(TESTS_DIR)

    # Build symbol pool across all scanned roots.
    funcs, classes, attrs = collect_defined_names(roots)
    defined = funcs | classes | attrs

    dcode_map = load_dcode_statuses()

    all_findings: list[Finding] = []
    scanned = 0
    for path in iter_py_files(roots):
        scanned += 1
        imported = collect_imported_names(path)
        # Check 1 is restricted to src/ -- scripts/ has too many short-lived
        # one-off comments to be worth auditing.
        if SRC_DIR in path.parents or path == REPO / "main.py":
            all_findings.extend(check_dead_comment_refs(path, defined, imported))
        # Check 2 runs everywhere -- deprecated D-codes shouldn't appear in
        # any production path.
        all_findings.extend(check_deprecated_dcode_use(path, dcode_map))
        # Check 3 runs everywhere -- intra-repo imports must resolve.
        all_findings.extend(check_broken_imports(path))

    if args.json:
        print(json.dumps([f.to_dict() for f in all_findings], indent=2))
        return 1 if (args.strict and all_findings) else 0

    # Human output
    print(f"Dead-code audit -- {scanned} .py files scanned")
    print(f"Symbol pool: {len(funcs)} functions, {len(classes)} classes, {len(attrs)} attributes")
    print(f"D-code registry: {len(dcode_map)} codes loaded "
          f"({sum(1 for v in dcode_map.values() if v.get('status', '').upper() in {'DEPRECATED', 'REVERTED', 'DELETED'})} flagged statuses)")
    print()

    if not all_findings:
        print("No findings.")
        return 0

    by_file: dict[str, list[Finding]] = defaultdict(list)
    for f in all_findings:
        by_file[f.file].append(f)

    dcode_findings = [f for f in all_findings if f.kind == "dead_dcode_use"]
    comment_findings = [f for f in all_findings if f.kind == "dead_comment_ref"]
    import_findings = [f for f in all_findings if f.kind == "broken_import"]

    if import_findings:
        print(f"=== {len(import_findings)} BROKEN INTRA-REPO IMPORTS (crash at import time) ===")
        for f in import_findings:
            print(f"  {f.file}:{f.line}  {f.target}  [{f.note}]")
            print(f"    | {f.context}")
        print()

    if dcode_findings:
        print(f"=== {len(dcode_findings)} references to DEPRECATED/REVERTED D-codes ===")
        for f in dcode_findings:
            print(f"  {f.file}:{f.line}  {f.target}  [{f.note}]")
            print(f"    | {f.context}")
        print()

    if comment_findings:
        print(f"=== {len(comment_findings)} suspect comment/docstring references ===")
        print("(triage: confirm the named symbol still exists; if not, fix or restore)")
        print()
        for fname in sorted(by_file):
            entries = [f for f in by_file[fname] if f.kind == "dead_comment_ref"]
            if not entries:
                continue
            print(f"  {fname}:")
            for f in entries[: args.max_per_file]:
                print(f"    L{f.line:>5}  {f.target}")
                print(f"           | {f.context}")
            if len(entries) > args.max_per_file:
                print(f"    ... +{len(entries) - args.max_per_file} more (use --json for full list)")
        print()

    print(f"Total findings: {len(all_findings)} "
          f"({len(import_findings)} import, {len(dcode_findings)} D-code, "
          f"{len(comment_findings)} comment)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
