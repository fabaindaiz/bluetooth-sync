"""The type stub of the extension (engine/crates/aurasync-engine/aurasync_engine.pyi) says what the
built module has: every public name in one is in the other, class by class.

The names with a leading underscore are out of both: the `test-panic` build's `_panic_outside_the_read`
and `_panic_next` exist only in the test build, and the stub's private aliases are not API.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import aurasync_engine
import pytest

STUB = Path(__file__).resolve().parents[2] / "engine/crates/aurasync-engine/aurasync_engine.pyi"


def public(names) -> set[str]:
    return {name for name in names if not name.startswith("_")}


def public_names_of_the_module() -> set[str]:
    """What `dir()` shows, without the extension submodule that maturin's package layout adds
    (`aurasync_engine/__init__.py` re-exports the compiled `aurasync_engine.aurasync_engine`)."""
    return public(name for name in dir(aurasync_engine) if not inspect.ismodule(getattr(aurasync_engine, name)))


@pytest.fixture(scope="module")
def stub() -> ast.Module:
    return ast.parse(STUB.read_text())


def test_the_stub_names_every_public_name_of_the_module_and_only_those(stub):
    declared = public(
        node.name for node in stub.body if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    )
    assert declared == public_names_of_the_module()


def test_each_class_of_the_stub_has_the_methods_of_the_built_class(stub):
    classes = [node for node in stub.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]
    assert classes
    for node in classes:
        built = getattr(aurasync_engine, node.name)
        declared = public(item.name for item in node.body if isinstance(item, ast.FunctionDef))
        # Exceptions inherit their methods; only what the class itself defines is compared.
        own = public(vars(built))
        assert declared == own, node.name


def test_the_stub_shipped_next_to_the_module_is_the_repository_file():
    """maturin copies the stub into the package as `__init__.pyi` with `py.typed`."""
    package = Path(aurasync_engine.__file__).parent
    assert (package / "py.typed").exists()
    assert (package / "__init__.pyi").read_text() == STUB.read_text()
