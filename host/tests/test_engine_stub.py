"""The type stub of the extension (engine/crates/aurasync-engine/aurasync_engine.pyi) says what the
built module has: every public name in one is in the other, class by class, and every signature
that the build carries (`__text_signature__`) has the stub's parameters, kinds and defaults.

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


def shape_of_stub(node: ast.FunctionDef) -> list[tuple[str, inspect._ParameterKind, bool]]:
    """Each parameter of a stub function as `(name, kind, has_default)`, without `self`."""
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [False] * (len(positional) - len(args.defaults)) + [True] * len(args.defaults)
    shape = [
        (arg.arg, inspect.Parameter.POSITIONAL_ONLY, default)
        for arg, default in zip(args.posonlyargs, defaults, strict=False)
    ]
    shape += [
        (arg.arg, inspect.Parameter.POSITIONAL_OR_KEYWORD, default)
        for arg, default in zip(args.args, defaults[len(args.posonlyargs) :], strict=True)
    ]
    if args.vararg is not None:
        shape.append((args.vararg.arg, inspect.Parameter.VAR_POSITIONAL, False))
    shape += [
        (arg.arg, inspect.Parameter.KEYWORD_ONLY, default is not None)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    ]
    if args.kwarg is not None:
        shape.append((args.kwarg.arg, inspect.Parameter.VAR_KEYWORD, False))
    return [entry for entry in shape if entry[0] != "self"]


def shape_of_built(built) -> list[tuple[str, inspect._ParameterKind, bool]]:
    """The same for a built callable, from the `__text_signature__` PyO3 writes, without `self`."""
    parameters = inspect.signature(built).parameters.values()
    return [
        (parameter.name, parameter.kind, parameter.default is not inspect.Parameter.empty)
        for parameter in parameters
        if parameter.name != "self"
    ]


def callables_of_the_stub(stub: ast.Module):
    """`(qualified name, stub function, built callable)` for every public function and method of the
    stub whose built counterpart carries a `__text_signature__` (a class's own, for `__init__`)."""
    for node in stub.body:
        if not isinstance(node, ast.FunctionDef | ast.ClassDef) or node.name.startswith("_"):
            continue
        built = getattr(aurasync_engine, node.name)
        if isinstance(node, ast.FunctionDef):
            yield node.name, node, built
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                if item.name == "__init__":
                    yield f"{node.name}()", item, built
                elif not item.name.startswith("_"):
                    yield f"{node.name}.{item.name}", item, getattr(built, item.name)


def test_each_signature_of_the_stub_is_the_built_one(stub):
    """Names, kinds (keyword-only, positional) and which parameters have a default, so a signature
    that drifts (a parameter renamed, made keyword-only, given a default) is caught, not only a
    name. Default values are not compared: PyO3 writes a constant's (`Reader`'s `max_block`) as
    `...`."""
    compared, unsigned = [], []
    for name, node, built in callables_of_the_stub(stub):
        if getattr(built, "__text_signature__", None) is None:
            unsigned.append(name)
            continue
        assert shape_of_stub(node) == shape_of_built(built), name
        compared.append(name)
    # PyO3 writes one for every function, constructor and method: none may go unchecked.
    assert unsigned == []
    assert "Reader()" in compared
    assert "TruePeakLimiter.configure" in compared
    assert "SpatialUpmix.set_params" in compared
    assert "capabilities" in compared


def test_the_signature_check_sees_a_drift():
    """The comparison itself: a keyword-only parameter turned positional, or a default dropped,
    differs."""
    stub = ast.parse("def configure(self, *, ceiling_db=None, release_ms=None): ...").body[0]
    assert shape_of_stub(stub) == shape_of_built(aurasync_engine.TruePeakLimiter.configure)
    for drifted in (
        "def configure(self, ceiling_db=None, release_ms=None): ...",
        "def configure(self, *, ceiling_db, release_ms=None): ...",
        "def configure(self, *, ceiling=None, release_ms=None): ...",
    ):
        assert shape_of_stub(ast.parse(drifted).body[0]) != shape_of_built(aurasync_engine.TruePeakLimiter.configure)


def test_the_stub_shipped_next_to_the_module_is_the_repository_file():
    """maturin copies the stub into the package as `__init__.pyi` with `py.typed`."""
    package = Path(aurasync_engine.__file__).parent
    assert (package / "py.typed").exists()
    assert (package / "__init__.pyi").read_text() == STUB.read_text()
