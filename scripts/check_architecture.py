"""Fail CI on forbidden cross-layer Python imports and bloated route entrypoints.

Use Python AST; model/provider integration is still developed incrementally.
This guard enforces hard boundaries without requiring extra runtime dependencies.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "services" / "api" / "app"

FORBIDDEN = {
    "domain": ("api", "infrastructure", "harness", "application", "llm", "tools"),
    "infrastructure": ("api",),
    "harness": ("api",),
    "application": ("api",),
}
# Legacy facades are kept intentionally for backward compatibility.


def module_name(file: Path) -> str:
    return ".".join(file.relative_to(APP.parent).with_suffix("").parts)


def resolve_import(module: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = module.split(".")[:-1]
    up = node.level - 1
    if up > len(parts):
        return ""
    return ".".join(parts[: len(parts) - up] + ([node.module] if node.module else []))


def check_python() -> list[str]:
    problems = []
    for file in APP.rglob("*.py"):
        src = file.read_text(encoding="utf-8")
        mod = module_name(file)
        layer = mod.split(".")[1] if len(mod.split(".")) > 1 else None
        denied = FORBIDDEN.get(layer, ())
        try:
            tree = ast.parse(src, filename=str(file))
        except SyntaxError as exc:
            problems.append(f"{file.relative_to(ROOT)}:{exc.lineno}: SyntaxError: {exc.msg}")
            continue
        targets = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets.extend((alias.name, node.lineno) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                targets.append((resolve_import(mod, node), node.lineno))
        for target, line in targets:
            for forbidden in denied:
                if target == f"app.{forbidden}" or target.startswith(f"app.{forbidden}."):
                    problems.append(
                        f"{file.relative_to(ROOT)}:{line}: {layer} cannot import {target}"
                    )
    return problems


def check_next_entrypoint() -> list[str]:
    page = ROOT / "apps" / "web" / "app" / "page.tsx"
    if page.exists() and len(page.read_text(encoding="utf-8").splitlines()) > 40:
        return ["apps/web/app/page.tsx must be a thin route entrypoint (<=40 lines)"]
    return []


def main() -> int:
    errors = check_python() + check_next_entrypoint()
    for error in errors:
        print("ARCH VIOLATION:", error)
    if errors:
        return 1
    print("Architecture boundaries: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
