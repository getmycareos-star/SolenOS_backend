"""Inventory static imports and call sites for backend app.services modules.

This is a source-level dependency map. It distinguishes imports from observed
call expressions and does not claim that a call succeeded at runtime.
"""
from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
SERVICE_PREFIX = "app.services."


def _module_name(file_path: Path) -> str:
    relative = file_path.relative_to(APP_ROOT.parent).with_suffix("")
    return ".".join(relative.parts)


def build_service_consumer_inventory() -> dict:
    consumers: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: {"imports": [], "calls": []}
    )
    for file_path in sorted(APP_ROOT.rglob("*.py")):
        if any(part in {"tests", "__pycache__"} for part in file_path.parts):
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        aliases: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(SERVICE_PREFIX):
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    aliases[local_name] = (node.module, alias.name)
                    key = (node.module, alias.name)
                    consumers[key]["imports"].append({
                        "consumer_module": _module_name(file_path),
                        "line": node.lineno,
                        "imported_as": local_name,
                    })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(SERVICE_PREFIX):
                        local_name = alias.asname or alias.name.split(".")[0]
                        aliases[local_name] = (alias.name, "*")
                        key = (alias.name, "*")
                        consumers[key]["imports"].append({
                            "consumer_module": _module_name(file_path),
                            "line": node.lineno,
                            "imported_as": local_name,
                        })

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_module = None
            called_symbol = None
            if isinstance(node.func, ast.Name) and node.func.id in aliases:
                called_module, called_symbol = aliases[node.func.id]
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                imported = aliases.get(node.func.value.id)
                if imported:
                    called_module = imported[0]
                    called_symbol = node.func.attr
            if called_module:
                key = (called_module, called_symbol or "*")
                consumers[key]["calls"].append({
                    "consumer_module": _module_name(file_path),
                    "line": node.lineno,
                    "call_expression": ast.unparse(node.func),
                })

    service_root = APP_ROOT / "services"
    for file_path in sorted(service_root.rglob("*.py")):
        if any(part in {"__pycache__"} for part in file_path.parts):
            continue
        module = _module_name(file_path)
        consumers.setdefault((module, "*"), {"imports": [], "calls": []})

    entries = []
    for (service_module, service_symbol), references in sorted(consumers.items()):
        imports = sorted(references["imports"], key=lambda item: (item["consumer_module"], item["line"]))
        calls = sorted(references["calls"], key=lambda item: (item["consumer_module"], item["line"]))
        entries.append({
            "service_module": service_module,
            "service_symbol": service_symbol,
            "import_count": len(imports),
            "observed_call_count": len(calls),
            "consumer_modules": sorted({item["consumer_module"] for item in calls}),
            "imports": imports,
            "calls": calls,
            "disposition": "callsite_found_runtime_invocation_verification_required" if calls
                else "import_found_no_direct_call_review_required" if imports
                else "no_import_or_call_dead_service_review_required",
        })
    return {
        "format_version": 1,
        "source": "AST scan of backend/app imports and call expressions",
        "service_reference_count": len(entries),
        "service_references": entries,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inventory = build_service_consumer_inventory()
    rendered = json.dumps(inventory, indent=2, sort_keys=True) + "\\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Exported {inventory['service_reference_count']} service references to {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
