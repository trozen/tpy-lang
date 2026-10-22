"""Which generator-expression walrus targets an occurrence outside the
generator expression resolves to, by Python's own scope resolution."""
from __future__ import annotations

import ast
import textwrap

import pytest

from .genexpr_walrus import genexpr_walrus_used_outside
from .nodes import ParseError


def _refused(source: str) -> list[str]:
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    found = genexpr_walrus_used_outside(tree, source)
    return sorted(w.target.id for gx in found for w in found[gx])  # type: ignore[attr-defined]


CASES = [
    ("function_read_after", """
        def f(xs):
            any((y := x) > 4 for x in xs)
            return y
        """, ["y"]),
    ("function_only_inside", """
        def f(xs):
            return any((y := x) > 4 and y < 9 for x in xs)
        """, []),
    ("nested_genexpr", """
        x = 0
        total = sum(sum((x := j) for j in range(3)) for i in range(2))
        """, ["x"]),
    ("nested_genexpr_outer_reads", """
        def f():
            return sum(x + sum((x := j) for j in range(3)) for i in range(2))
        """, ["x"]),
    ("module_function_reads_global", """
        found = any((y := x) > 4 for x in [1, 5])
        def helper():
            return y
        """, ["y"]),
    ("module_function_own_local", """
        found = any((y := x) > 4 for x in [1, 5])
        def helper():
            y = 10
            return y
        """, []),
    ("nested_def_parameter", """
        def main(xs):
            found = any((y := x) > 4 for x in xs)
            def h(y):
                return y + 1
            return found, h(2)
        """, []),
    ("module_function_parameter", """
        def f(y):
            return y + 1
        print(sum((y := i) for i in range(3)))
        """, []),
    ("global_declaration", """
        found = any((y := x) > 4 for x in [1, 5])
        def helper():
            global y
            y = 3
        """, ["y"]),
    ("nonlocal_declaration", """
        def f(xs):
            found = any((y := x) > 4 for x in xs)
            def g():
                nonlocal y
                y = 1
            return found
        """, ["y"]),
    ("free_through_nested_def", """
        def f(xs):
            found = any((y := x) > 4 for x in xs)
            def g():
                def h():
                    return y
                return h
            return found
        """, ["y"]),
    ("inner_def_shadows_for_its_child", """
        def f(xs):
            found = any((y := x) > 4 for x in xs)
            def g():
                y = 2
                def h():
                    return y
                return h
            return found
        """, []),
    ("function_own_name", """
        def hit():
            print(sum((hit := i + 1) for i in range(3)))
        hit()
        """, []),
    ("genexpr_in_default_binds_parent", """
        x = 0
        def f(a=sum((x := i) for i in range(3))):
            return a
        print(x)
        """, ["x"]),
    ("genexpr_in_default_parent_unused", """
        def f(a=sum((x := i) for i in range(3))):
            x = 1
            return a + x
        """, []),
    ("genexpr_in_lambda_default", """
        def outer():
            x = 0
            f = lambda a=sum((x := i) for i in range(3)): a
            return x
        """, ["x"]),
    ("sibling_comprehension_loop_variable", """
        def f(xs):
            a = [y for y in xs]
            return sum((y := x) for x in xs), a
        """, []),
    ("sibling_comprehension_reads_it", """
        def f(xs):
            t = sum((y := x) for x in xs)
            return [y + x for x in xs]
        """, ["y"]),
    ("class_body_reads_it", """
        def f(xs):
            t = sum((y := x) for x in xs)
            class C:
                w = y
            return C
        """, ["y"]),
    ("class_body_binds_its_own", """
        def f(xs):
            t = sum((y := x) for x in xs)
            class C:
                y = 1
            return C
        """, []),
    ("module_class_body_binds_its_own", """
        t = sum((y := x) for x in [1, 2])
        class C:
            y = 1
        """, []),
    ("method_reads_global", """
        t = sum((y := x) for x in [1, 2])
        class C:
            def m(self):
                return y
        """, ["y"]),
    ("method_skips_class_binding", """
        def f(xs):
            t = sum((y := x) for x in xs)
            class C:
                y = 1
                def m(self):
                    return y
            return C
        """, ["y"]),
    # A sibling generator expression that only stores the name writes its
    # own frame too, so nothing observes either store.
    ("sibling_genexprs_write_only", """
        def f(xs):
            a = sum((y := x) for x in xs)
            b = sum((y := x + 1) for x in xs)
            return a + b
        """, []),
    ("module_sibling_genexprs_write_only", """
        a = sum((y := x) for x in [1, 2])
        b = sum((y := x * 2) for x in [1, 2])
        """, []),
    # The reader may see the writer's last value; only the writer is refused.
    ("sibling_genexpr_reads_it", """
        def f(xs):
            a = sum((y := x) for x in xs)
            b = sum((y if x else 0) + (y := x) for x in xs)
            return a + b
        """, ["y"]),
    ("store_before_unread", """
        def f(xs):
            y = 0
            return sum((y := x) for x in xs)
        """, ["y"]),
    ("aug_assign_after", """
        def f(xs):
            y = 0
            t = sum((y := x) for x in xs)
            y += 1
            return y
        """, ["y"]),
    ("first_iterable_reads_it", """
        y = 5
        def f(xs):
            return sum((y := x) for x in g(y))
        """, ["y"]),
    ("del_after", """
        def f(xs):
            t = sum((y := x) for x in xs)
            del y
        """, ["y"]),
    ("module_del_after", """
        t = sum((y := x) for x in [1, 2])
        del y
        """, ["y"]),
    ("except_as", """
        def f(xs):
            t = sum((y := x) for x in xs)
            try:
                pass
            except ValueError as y:
                pass
        """, ["y"]),
    ("match_capture", """
        def f(xs, v):
            t = sum((y := x) for x in xs)
            match v:
                case y:
                    pass
        """, ["y"]),
    ("match_as", """
        def f(xs, v):
            t = sum((y := x) for x in xs)
            match v:
                case [1] as y:
                    pass
        """, ["y"]),
    ("match_mapping_rest", """
        t = sum((y := x) for x in [1, 2])
        match {}:
            case {**y}:
                pass
        """, ["y"]),
    ("decorator_genexpr_read_later", """
        def deco(n):
            return lambda f: f
        @deco(sum((y := x) for x in [1, 2]))
        def g():
            pass
        print(y)
        """, ["y"]),
    ("parameter_annotation_reads_it", """
        t = sum((y := x) for x in [1, 2])
        def g(a: y) -> None:
            pass
        """, ["y"]),
    ("parameter_annotation_unevaluated", """
        from __future__ import annotations
        t = sum((y := x) for x in [1, 2])
        def g(a: y) -> None:
            pass
        """, []),
    ("lambda_parameter_same_name", """
        t = sum((y := x) for x in [1, 2])
        f = lambda y: y + 1
        """, []),
    ("lambda_binding_scope_unused", """
        f = lambda xs: sum((y := x) for x in xs)
        """, []),
    ("lambda_binding_scope_reads_it", """
        f = lambda xs: (sum((y := x) for x in xs), y)
        """, ["y"]),
    ("type_param_of_def", """
        print(sum((T := x) for x in range(3)))
        def ident[T](x: T) -> T:
            return x
        """, []),
    ("type_param_of_alias", """
        type A[y] = list[y]
        ok = any((y := x) > 4 for x in [1, 5])
        """, []),
    ("private_name_mangled_parameter", """
        print(sum((__y := x) for x in range(3)))
        class C:
            def m(self, __y: int) -> int:
                return __y
        """, []),
    ("private_name_mangled_global_read", """
        print(sum((_C__y := x) for x in range(3)))
        class C:
            def m(self):
                return __y
        """, ["_C__y"]),
    ("method_private_target_read_after", """
        class C:
            def m(self, xs):
                t = sum((__y := x) for x in xs)
                return __y
        """, ["__y"]),
    ("injected_name_collision", """
        _tpy_w0 = 3
        _tpy_scope_marker_0_ = 4
        def f() -> int:
            global _tpy_w0, _tpy_scope_marker_0_
            return _tpy_w0 + _tpy_scope_marker_0_
        print(sum((y := x) for x in range(3)))
        """, []),
    # A lambda inside the generator expression binds the name itself (its
    # own genexpr walrus), so its later read is a use of the lambda's scope.
    ("lambda_inside_binds_its_own_and_reads", """
        def f(xs):
            return sum((y := x) + call(lambda: sum((y := z) for z in [1, 2]) * 0 + y)
                       for x in xs)
        """, ["y"]),
    ("lambda_inside_walrus_then_genexpr", """
        print(sum((y := i) + call(lambda: (y := 100) + sum((y := j) for j in range(3)) + y)
                  for i in range(2)))
        """, ["y"]),
    ("lambda_inside_reads_frame_local", """
        def f(xs):
            return sum((y := x) + call(lambda: y) for x in xs)
        """, []),
    ("comprehension_inside_own_loop_variable", """
        def f(xs):
            return sum((y := x) + len([y for y in xs]) for x in xs)
        """, []),
    ("mangled_spellings_one_variable", """
        class C:
            def f(self):
                a = sum((__y := i) for i in range(3))
                b = sum((_C__y := i) for i in range(3))
                return a + b
        """, []),
    ("mangled_spellings_read_after", """
        class C:
            def f(self):
                a = sum((__y := i) for i in range(3))
                return _C__y
        """, ["__y"]),
    ("class_body_reads_before_binding", """
        print(sum((y := x) for x in range(3)))
        class C:
            z = y
            y = 20
        """, ["y"]),
    ("listcomp_walrus_not_in_genexpr", """
        def f(xs):
            a = [(y := x) for x in xs]
            return y
        """, []),
    ("listcomp_in_genexpr", """
        def f(xs):
            a = sum(len([(y := x) for x in xs]) for _ in range(2))
            return y
        """, ["y"]),
    ("import_binding", """
        def f(xs):
            t = sum((y := x) for x in xs)
            import os as y
        """, ["y"]),
]


@pytest.mark.parametrize("source,expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_used_outside(source: str, expected: list[str]) -> None:
    assert _refused(source) == expected


def test_no_genexpr_walrus_skips_symtable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object) -> None:
        raise AssertionError("symtable ran")
    monkeypatch.setattr("tpyc.parse.genexpr_walrus.respelled_symtable", fail)
    assert _refused("y = 1\nt = sum(x for x in [1])\nz = [(w := 1) for _ in [1]]\n") == []


def test_comprehension_iterable_walrus_is_python_syntax_error() -> None:
    with pytest.raises(ParseError, match="comprehension iterable expression"):
        _refused("t = sum(x for x in [(y := 2)])\n")


def test_class_body_walrus_is_python_syntax_error() -> None:
    with pytest.raises(ParseError, match="class body"):
        _refused("""
            class C:
                t = sum((y := x) for x in [1, 2])
            """)
