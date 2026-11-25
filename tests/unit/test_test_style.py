"""The test suite's own standard, enforced: Arrange-Act-Assert with one assert per test.

Every tests/**/test_*.py file is parsed with `ast` (never imported) and checked against the
rules in CLAUDE.md "Test standard". A failure names the file and the offending tests, so the
standard can't drift back on a later change.
"""

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
TEST_FILES = sorted(TESTS_DIR.rglob("test_*.py"))
# Words that say nothing about behaviour (rule 2): a name must state a condition or outcome.
VAGUE_WORDS = frozenset({"works", "correct", "stuff"})
MIN_NAME_WORDS = 3


def _rel(path: Path) -> str:
    return path.relative_to(TESTS_DIR).as_posix()


def _tests(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


def _asserts(test: ast.AST) -> int:
    return sum(isinstance(node, ast.Assert) for node in ast.walk(test))


def _asserts_by_raising(test: ast.AST) -> bool:
    # pytest.raises, or an _exploding fake that raises if called, counts as the assert.
    source = ast.unparse(test)
    return "pytest.raises" in source or "_exploding" in source


def _assert_offenders(path: Path) -> list[str]:
    return [
        t.name
        for t in _tests(path)
        if _asserts(t) > 1 or (_asserts(t) == 0 and not _asserts_by_raising(t))
    ]


def _logic_offenders(path: Path) -> list[str]:
    return [
        t.name
        for t in _tests(path)
        if any(
            isinstance(node, ast.For | ast.AsyncFor | ast.While | ast.If) for node in ast.walk(t)
        )
    ]


def _is_parametrize(decorator: ast.expr) -> bool:
    return isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", "") == "parametrize"


def _ids_offenders(path: Path) -> list[str]:
    return [
        t.name
        for t in _tests(path)
        if any(
            isinstance(d, ast.Call)
            and _is_parametrize(d)
            and not any(k.arg == "ids" for k in d.keywords)
            for d in t.decorator_list
        )
    ]


def _name_offenders(path: Path) -> list[str]:
    return [
        t.name
        for t in _tests(path)
        if len(t.name.removeprefix("test_").split("_")) < MIN_NAME_WORDS
        or VAGUE_WORDS & set(t.name.split("_"))
    ]


# ---------- the suite itself ----------


def test_style_guard_finds_the_test_files() -> None:
    # Sanity check: a wrong TESTS_DIR would make every check below pass vacuously.
    assert len(TEST_FILES) > 20


# ---------- rule 1: one assert per test ----------


@pytest.mark.parametrize("path", TEST_FILES, ids=_rel)
def test_style_each_test_has_exactly_one_assert(path: Path) -> None:
    assert _assert_offenders(path) == []


# ---------- rule 9: no logic in test bodies ----------


@pytest.mark.parametrize("path", TEST_FILES, ids=_rel)
def test_style_test_bodies_have_no_if_or_loop(path: Path) -> None:
    assert _logic_offenders(path) == []


# ---------- rule 8: readable parametrize ids ----------


@pytest.mark.parametrize("path", TEST_FILES, ids=_rel)
def test_style_parametrize_always_names_its_cases(path: Path) -> None:
    assert _ids_offenders(path) == []


# ---------- rule 2: names describe behaviour ----------


@pytest.mark.parametrize("path", TEST_FILES, ids=_rel)
def test_style_test_names_describe_the_behaviour(path: Path) -> None:
    assert _name_offenders(path) == []
