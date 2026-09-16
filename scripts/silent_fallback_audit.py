"""
Silent-fallback retrospective audit (READ-ONLY).
Finds try/except blocks whose handler body matches one of:
  S1: return empty/default
  S2: pass / continue / Ellipsis
  S3: log-then-fall-through (no return after log)
  S4: log-and-return-empty/default
"""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INCLUDE_GLOBS = [
    ROOT / "src",
    ROOT / "config",
    ROOT / "scripts",
]
EXTRA_FILES = [ROOT / "main.py"]

EXCLUDE_PARTS = {"tests", "mx-arena", "docs", "data", "logs", "models",
                 "__pycache__", ".venv", "node_modules", ".claude"}

EMPTY_LITERALS = {
    ast.Dict: lambda n: not n.keys,           # {}
    ast.List: lambda n: not n.elts,           # []
    ast.Set:  lambda n: not n.elts,           # set()
    ast.Tuple: lambda n: not n.elts,          # ()
}

def _is_empty_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        if node.value is None: return True
        if node.value is False: return True
        if node.value == 0: return True
        if node.value == "": return True
        if node.value == 0.0: return True
    return False

def _is_empty_collection(node: ast.AST) -> bool:
    for t, pred in EMPTY_LITERALS.items():
        if isinstance(node, t):
            try: return pred(node)
            except Exception: return False
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in ("dict", "list", "set", "tuple") and not node.args and not node.keywords:
            return True
    return False

def _is_logger_call(node: ast.AST) -> bool:
    """Match calls like logger.info / log.debug / self.logger.warning / logging.warn."""
    if not isinstance(node, ast.Call): return False
    fn = node.func
    if isinstance(fn, ast.Attribute):
        attr = fn.attr.lower()
        if attr in {"debug", "info", "warning", "warn", "error", "exception", "critical"}:
            # accept any value for the receiver
            return True
    return False

def _stmt_is_logger(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and _is_logger_call(stmt.value)

def _stmt_is_return_default(stmt: ast.stmt) -> bool | str:
    if not isinstance(stmt, ast.Return): return False
    val = stmt.value
    if val is None:
        return "return None (implicit)"
    if _is_empty_constant(val):
        return f"return {ast.unparse(val)}"
    if _is_empty_collection(val):
        return f"return {ast.unparse(val)}"
    # return SomeClass()  -- empty constructor
    if isinstance(val, ast.Call) and not val.args and not val.keywords:
        try: return f"return {ast.unparse(val)}"
        except Exception: return False
    # return some_var (likely a default container declared above) — we'll flag conservatively
    if isinstance(val, ast.Name):
        return f"return {val.id}  (var)"
    return False

def _stmt_is_pass_like(stmt: ast.stmt) -> str | bool:
    if isinstance(stmt, ast.Pass): return "pass"
    if isinstance(stmt, ast.Continue): return "continue"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return "..."
    return False

def classify_handler(h: ast.ExceptHandler):
    """Return (class, returns_str, log_text) or None."""
    body = h.body
    if not body: return None
    # strip trailing raise — if there's a raise we don't consider it silent
    if any(isinstance(s, ast.Raise) for s in body):
        return None

    # S2 — single pass-like statement
    if len(body) == 1:
        pl = _stmt_is_pass_like(body[0])
        if pl: return ("S2", pl, None)
        rd = _stmt_is_return_default(body[0])
        if rd: return ("S1", rd, None)
        # single logger call only -> S3
        if _stmt_is_logger(body[0]):
            return ("S3", "fall-through (None)", ast.unparse(body[0])[:120])

    # multi-statement: pattern is logger(s) then return default
    log_lines = []
    for s in body[:-1]:
        if _stmt_is_logger(s):
            log_lines.append(ast.unparse(s)[:120])
            continue
        # allow simple counter increments / assigns to local — keep scanning
        if isinstance(s, (ast.Assign, ast.AugAssign, ast.Expr)):
            continue
        # anything else (call to retry, raise via wrapper, etc.) -> not a clean silent fallback
        log_lines = None
        break

    last = body[-1]
    rd = _stmt_is_return_default(last)
    if log_lines is not None and log_lines and rd:
        return ("S4", rd, " | ".join(log_lines))
    if log_lines is not None and log_lines and isinstance(last, ast.Expr) and _stmt_is_logger(last):
        # all logger calls, no return -> S3
        log_lines.append(ast.unparse(last)[:120])
        return ("S3", "fall-through (None)", " | ".join(log_lines))
    if log_lines is None and rd and len(body) == 1:
        # already handled above, but defensive
        return ("S1", rd, None)
    # multi-stmt, no return, but contains pass/continue at end
    pl = _stmt_is_pass_like(last)
    if log_lines is not None and pl:
        return ("S2", pl, " | ".join(log_lines) if log_lines else None)
    return None

def get_caught(h: ast.ExceptHandler) -> str:
    if h.type is None: return "bare except"
    try:
        return ast.unparse(h.type)
    except Exception:
        return "?"

def find_enclosing_func(tree: ast.AST, lineno: int) -> str:
    name = "<module>"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                # prefer most-specific (deepest)
                if name == "<module>" or node.lineno > _func_start.get(name, -1):
                    name = node.name
                    _func_start[name] = node.lineno
    return name

_func_start: dict = {}

def walk_files():
    for base in INCLUDE_GLOBS:
        if not base.exists(): continue
        for p in base.rglob("*.py"):
            parts = set(p.parts)
            if parts & EXCLUDE_PARTS: continue
            yield p
    for p in EXTRA_FILES:
        if p.exists(): yield p

def analyze_file(path: Path):
    findings = []
    try:
        src = path.read_text(encoding="utf-8")
    except Exception:
        return findings
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return findings

    # Build parent map for function lookup
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def enclosing_func(node):
        cur = node
        while id(cur) in parents:
            cur = parents[id(cur)]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
        return "<module>"

    def enclosing_class(node):
        cur = node
        while id(cur) in parents:
            cur = parents[id(cur)]
            if isinstance(cur, ast.ClassDef):
                return cur.name
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            try_start = node.lineno
            for h in node.handlers:
                cls = classify_handler(h)
                if not cls: continue
                klass, returns, log_text = cls
                fn = enclosing_func(h)
                cls_name = enclosing_class(h)
                qual = f"{cls_name}.{fn}" if cls_name else fn
                findings.append({
                    "path": str(path).replace("\\", "/"),
                    "rel": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "try_line": try_start,
                    "except_line": h.lineno,
                    "except_end": getattr(h, "end_lineno", h.lineno),
                    "func": qual,
                    "class": klass,
                    "caught": get_caught(h),
                    "returns": returns,
                    "log": log_text or "",
                })
    return findings

def main():
    all_findings = []
    files_scanned = 0
    for p in walk_files():
        files_scanned += 1
        all_findings.extend(analyze_file(p))
    out = ROOT / "scripts" / "_silent_fallback_findings.json"
    out.write_text(json.dumps({
        "files_scanned": files_scanned,
        "total": len(all_findings),
        "findings": all_findings,
    }, indent=2))
    print(f"Scanned {files_scanned} files, found {len(all_findings)} silent-fallback catches")
    # Class breakdown
    by_class = {}
    for f in all_findings:
        by_class[f["class"]] = by_class.get(f["class"], 0) + 1
    print("By class:", by_class)
    print(f"Wrote {out}")

if __name__ == "__main__":
    main()
