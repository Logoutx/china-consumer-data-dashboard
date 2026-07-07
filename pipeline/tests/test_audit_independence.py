"""Mechanically enforces Gate B's whole reason for existing: pipeline/audit/**
must never import pipeline.parsers, pipeline.normalize, pipeline.build, or
pipeline.takeaways, and must never reference pipeline/config/field_map.yaml.
Walks every module's AST rather than grepping importlib at runtime, so it
also catches an import that's never actually executed on the current code
path (e.g. behind an `if` a test run wouldn't exercise).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

AUDIT_PKG = Path(__file__).resolve().parent.parent / "audit"
FORBIDDEN_MODULES = {"pipeline.parsers", "pipeline.normalize", "pipeline.build", "pipeline.takeaways"}
FORBIDDEN_MODULE_ROOTS = {name.split(".")[-1] for name in FORBIDDEN_MODULES}  # "parsers", "normalize", "build", "takeaways"


def _audit_py_files() -> list[Path]:
    return sorted(AUDIT_PKG.rglob("*.py"))


def _imported_module_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # from pipeline import build  -> module="pipeline", also record "pipeline.build"
                names.add(node.module)
                if node.module == "pipeline":
                    for alias in node.names:
                        names.add(f"pipeline.{alias.name}")
                else:
                    for alias in node.names:
                        names.add(f"{node.module}.{alias.name}")
    return names


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id()s of Constant nodes that are a module/class/function docstring (a
    bare string as the first statement of that body) -- these are prose, not
    a code reference to a path, and must be excluded from the field_map.yaml
    scan below or every module's own explanatory docstring (which necessarily
    quotes "field_map.yaml" to explain the constraint) would trip the check."""
    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstring_ids.add(id(body[0].value))
    return docstring_ids


def _string_literals(tree: ast.AST) -> list[str]:
    """Every string literal that is NOT a docstring -- i.e. a literal actually
    used as a value in code (an argument, an assignment, a dict key/value),
    which is what a real "open pipeline/config/field_map.yaml" reference would
    look like."""
    docstring_ids = _docstring_nodes(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids
    ]


@pytest.mark.parametrize("path", _audit_py_files(), ids=lambda p: str(p.relative_to(AUDIT_PKG)))
def test_no_forbidden_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = _imported_module_names(tree)
    hits = {name for name in imported if name in FORBIDDEN_MODULES}
    assert not hits, f"{path} imports forbidden module(s): {hits}"


@pytest.mark.parametrize("path", _audit_py_files(), ids=lambda p: str(p.relative_to(AUDIT_PKG)))
def test_no_field_map_yaml_reference(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    literals = _string_literals(tree)
    hits = [s for s in literals if "field_map.yaml" in s or "field_map" in s]
    assert not hits, f"{path} references field_map.yaml: {hits}"


def test_audit_package_has_no_direct_pipeline_star_import():
    """Defends against `from pipeline import *` (which would sidestep the
    per-name check above) anywhere in the package."""
    for path in _audit_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pipeline":
                star = any(alias.name == "*" for alias in node.names)
                assert not star, f"{path} does `from pipeline import *`"


def test_forbidden_module_list_is_nonempty():
    """Guards against this test file itself silently becoming a no-op (e.g. a
    future refactor emptying FORBIDDEN_MODULES by accident)."""
    assert FORBIDDEN_MODULES == {"pipeline.parsers", "pipeline.normalize", "pipeline.build", "pipeline.takeaways"}


def test_at_least_one_audit_module_exists():
    assert len(_audit_py_files()) >= 10, "expected the gate_b package to have grown past a handful of modules"
