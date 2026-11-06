"""Tests for the loop half of `analyze_last_uses`'s `repeated_reads`.

A read inside a loop body, or in a `while` condition, of a name live at the
loop's header may run again on the same binding when no binding of the name
lies between it and the next iteration (`holders.LoopCarry`). Such a read is
never a last use either way; `repeated_reads` is what a consuming read asks.
"""

from __future__ import annotations

from tpyc.identity_map import IdentitySet
from tpyc.liveness import _collect_reads_expr, analyze_last_uses
from tpyc.parse.nodes import TpyStmt
from tpyc.parse.parser import Parser


def _reads(src: str, name: str = "it") -> list[tuple[bool, bool]]:
    """(repeated, last use) for each read of `name`, in source order."""
    fn = Parser().parse(src, module_name="m", is_entry_point=True).functions[0]
    repeated = IdentitySet()
    last = analyze_last_uses(fn.body, repeated_reads=repeated)
    out: list[tuple[bool, bool]] = []

    def walk(stmts: list[TpyStmt]) -> None:
        for st in stmts:
            for e in st.exprs():
                for n in _collect_reads_expr(e):
                    if n.name == name:
                        out.append((n in repeated, n in last))
            for body in st.sub_bodies():
                walk(body)
    walk(fn.body)
    return out


def test_a_loop_body_read_of_an_outer_name_repeats():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(3):
        g(it)
''') == [(True, False)]


def test_a_while_condition_read_repeats():
    assert _reads('''
def f(it: int) -> None:
    while h(it):
        pass
''') == [(True, False)]


def test_a_for_head_source_is_read_once():
    assert _reads('''
def f(it: int) -> None:
    for x in it:
        g(x)
''') == [(False, True)]


def test_a_read_followed_by_break_does_not_repeat():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(3):
        g(it)
        break
''') == [(False, True)]


def test_a_binding_before_the_read_in_the_body_starts_afresh():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(3):
        it = mk()
        g(it)
''') == [(False, True)]


def test_a_binding_after_the_read_ends_the_carry():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(3):
        g(it)
        it = mk()
''') == [(False, True)]


def test_a_self_binding_reads_the_previous_iteration_s_value():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(3):
        it = g(it)
''') == [(False, False)]


def test_a_binding_on_one_path_only_still_repeats():
    assert _reads('''
def f(it: int, c: bool) -> None:
    for _ in range(3):
        if c:
            it = mk()
        g(it)
''') == [(True, False)]


def test_an_outer_loop_carries_through_an_inner_one():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(3):
        for _ in range(2):
            g(it)
''') == [(True, False)]


def test_a_return_through_a_finally_leaves_the_loop():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(2):
        try:
            g(it)
            return
        finally:
            pass
''') == [(False, False)]


def test_an_exception_through_a_finally_to_a_handler_in_the_loop_repeats():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(2):
        try:
            try:
                g(it)
                raise E()
            finally:
                pass
        except E:
            pass
''') == [(True, False)]


def test_a_finally_that_continues_keeps_a_return_in_the_loop():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(2):
        try:
            g(it)
            return
        finally:
            continue
''') == [(True, False)]


def test_a_break_through_a_finally_leaves_the_loop():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(2):
        try:
            g(it)
            break
        finally:
            pass
''') == [(False, False)]


def test_a_finally_that_may_break_keeps_a_return_out_of_the_loop():
    assert _reads('''
def f(it: int, stop: bool) -> None:
    for _ in range(2):
        try:
            g(it)
            return
        finally:
            if stop:
                break
''') == [(False, False)]


def test_a_break_in_a_finally_keeps_the_enclosing_loop_s_carries():
    assert _reads('''
def f(it: int) -> None:
    for _ in range(2):
        for _ in range(2):
            try:
                g(it)
                return
            finally:
                break
''') == [(True, False)]
