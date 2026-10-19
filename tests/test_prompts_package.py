"""The prompts package: every name prompts.py had is still there, each submodule exports exactly what it defines (the
same objects, so `prompts.X` and `prompts.<area>.X` never differ), and no module is longer than a reader can hold."""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

from local_assistant import prompts

# every public name prompts.py defined, and `_ANY_LANGUAGE`, the one private name a test reads; taken from the module's
# own top-level definitions before the split (its imports, such as `Sequence`, are not names it offers)
NAMES = Path(__file__).parent / "data/prompts_public_names.txt"
FOLDER = Path(__file__).resolve().parent.parent / "src/local_assistant/prompts"


def package_files() -> list[Path]:
    """The files that hold the prompts: prompts.py, or every file of the package prompts/."""
    return sorted(FOLDER.glob("*.py")) if FOLDER.is_dir() else [FOLDER.with_suffix(".py")]


def defined_names(path: Path) -> set[str]:
    """The public names a module file defines at its top level (what it imports is not its own)."""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        match node:
            case ast.FunctionDef(name=name) | ast.ClassDef(name=name):
                names.add(name)
            case ast.Assign(targets=targets):
                names.update(target.id for target in targets if isinstance(target, ast.Name))
            case ast.AnnAssign(target=ast.Name(id=name)):
                names.add(name)
    return {name for name in names if not name.startswith("_")}


def test_every_name_the_module_had_is_still_there() -> None:
    expected = set(NAMES.read_text(encoding="utf-8").split())
    assert expected <= set(dir(prompts)), sorted(expected - set(dir(prompts)))


def test_each_submodule_exports_exactly_its_public_names() -> None:
    for info in pkgutil.iter_modules(getattr(prompts, "__path__", [])):
        module = importlib.import_module(f"local_assistant.prompts.{info.name}")
        assert module.__file__ is not None
        assert set(module.__all__) == defined_names(Path(module.__file__)), info.name
        for name in module.__all__:
            assert getattr(prompts, name) is getattr(module, name), f"prompts.{name} is not prompts.{info.name}.{name}"


def test_no_prompts_module_is_over_800_lines() -> None:
    for path in package_files():
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 800, path
