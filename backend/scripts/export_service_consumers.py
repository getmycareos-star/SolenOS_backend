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
    if relative.name == "__init__":
        relative = relative.parent
    return ".".join(relative.parts)


def build_service_consumer_inventory() -> dict:
    consumers: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: {"imports": [], "calls": []}
    )
    skipped_files = []
    source_files = sorted(APP_ROOT.rglob("*.py"))
    for file_path in source_files:
        if any(part in {"tests", "__pycache__"} for part in file_path.parts):
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            skipped_files.append({
                "path": str(file_path.relative_to(APP_ROOT.parent)).replace("\\\\", "/"),
                "reason": f"{type(exc).__name__}: {exc}",
            })
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
            attribute_parts = []
            call_root = node.func
            while isinstance(call_root, ast.Attribute):
                attribute_parts.append(call_root.attr)
                call_root = call_root.value
            if not isinstance(call_root, ast.Name) or call_root.id not in aliases:
                continue
            imported_module, imported_symbol = aliases[call_root.id]
            called_module = imported_module
            if imported_symbol == "*":
                called_symbol = attribute_parts[0] if attribute_parts else "*"
            else:
                called_symbol = imported_symbol
                if attribute_parts:
                    called_symbol += "." + ".".join(reversed(attribute_parts))
            key = (called_module, called_symbol)
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
        "source_file_count": len(source_files),
        "skipped_file_count": len(skipped_files),
        "skipped_files": skipped_files,
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
