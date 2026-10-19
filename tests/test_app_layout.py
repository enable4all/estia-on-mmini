"""The layout of app.py and its four hosts: how long App.__init__ is, which class holds which methods, and that App
keeps one-line delegates for the names the tests and the call manager use."""

import ast
from pathlib import Path

from local_assistant import app as app_module
from local_assistant import lifecycle

SRC = Path(__file__).resolve().parent.parent / "src/local_assistant"


def klass(path: Path, cls: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text())
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls)


def method(path: Path, cls: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in klass(path, cls).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def names_of(path: Path, cls: str) -> set[str]:
    return {node.name for node in klass(path, cls).body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def lines_of(node: ast.stmt) -> int:
    return (node.end_lineno or node.lineno) - node.lineno + 1


def test_app_init_is_a_short_composition_root() -> None:
    init = method(SRC / "app.py", "App", "__init__")
    assert lines_of(init) <= 60
    calls = [
        n.func.attr
        for n in ast.walk(init)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        and n.func.attr.startswith("_wire_")
    ]
    assert calls == [
        "_wire_core",
        "_wire_mail",
        "_wire_calendar_and_digest",
        "_wire_extras",
        "_wire_voice_and_calls",
        "_wire_reminders_and_initiative",
        "_wire_tools_and_brain",
        "_wire_commands",
    ]


def test_the_call_methods_live_on_call_host_and_app_only_delegates() -> None:
    app = SRC / "app.py"
    assert {
        "call_started",
        "call_ended",
        "call_turn",
        "call_warm_up",
        "practice_call_ended",
        "call_drop_waiting",
        "call_interrupted",
        "call_note",
        "call_chords",
        "call_summary",
        "glance_now",
        "rundown",
    } <= names_of(SRC / "call_host.py", "CallHost")
    for name in ("call_started", "call_ended", "call_warm_up"):
        assert lines_of(method(app, "App", name)) <= 4  # a delegate: its def and one line
    # call_turn's signature runs over 120 columns, so ruff format spreads it one parameter per line
    assert lines_of(method(app, "App", "call_turn")) <= 12
    stored = {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }
    assert not {"_glance", "_calls", "_call_chords", "_chords_after"} & stored  # the host owns the call's state


def test_the_document_methods_live_on_documents_host() -> None:
    app = SRC / "app.py"
    assert {
        "save_documents",
        "docs_command",
        "send_document",
        "documents_forever",
        "documents_tick",
    } <= names_of(SRC / "documents_host.py", "DocumentsHost")
    assert lines_of(method(app, "App", "documents_tick")) <= 4  # a delegate
    assert "_documents_quiet" not in {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }


def test_start_up_and_upkeep_live_on_lifecycle() -> None:
    app = SRC / "app.py"
    assert {
        "name_profile",
        "profile_picture",
        "welcome_text",
        "welcome",
        "recover",
        "offline_note",
        "is_stale",
        "reminders_start_up",
        "initiative_start_up",
        "music_reminder",
        "sweep_files",
        "sweeper",
        "retention_pass",
        "retention_loop",
        "mark_alive",
        "heartbeat",
    } <= names_of(SRC / "lifecycle.py", "Lifecycle")
    assert lines_of(method(app, "App", "sweep_files")) <= 4  # a delegate
    assert not {"_started_at", "_offline_since", "_offline_noted"} & {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }
    # the state keys are the lifecycle's: the app hands on the same objects
    assert app_module.ALIVE_KEY is lifecycle.ALIVE_KEY
    assert app_module.ALIVE_SECONDS == lifecycle.ALIVE_SECONDS
    assert app_module.WELCOME_KEY is lifecycle.WELCOME_KEY


def test_the_status_methods_live_on_status_host() -> None:
    app = SRC / "app.py"
    assert {
        "capability_lines",
        "security_level_loop",
        "status_lines",
        "status",
        "problems_loop",
        "on_problem",
        "linked_devices_loop",
        "check_linked_devices",
    } <= names_of(SRC / "status_host.py", "StatusHost")
    assert lines_of(method(app, "App", "status")) <= 4  # a delegate
    assert not {"_security_level", "_read_security_level"} & {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }


def test_app_and_the_hosts_are_readable_sizes() -> None:
    sizes = {
        name: len((SRC / f"{name}.py").read_text().splitlines())
        for name in ("app", "call_host", "documents_host", "lifecycle", "status_host")
    }
    assert sizes["app"] <= 1750, sizes
    assert sizes["call_host"] <= 450 and sizes["lifecycle"] <= 350, sizes
    assert sizes["status_host"] <= 250 and sizes["documents_host"] <= 200, sizes
