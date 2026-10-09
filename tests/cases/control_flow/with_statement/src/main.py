# with statement: __enter__/__exit__ protocol, multiple managers, name reuse, post-with visibility
from typing import Callable, Self

from tpy import int32


class Logger:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> Self:
        print(f"enter {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit {self.name}")

    def log(self, msg: str) -> None:
        print(f"[{self.name}] {msg}")


class Connection:
    active: bool

    def __init__(self) -> None:
        self.active = True

    def __enter__(self) -> str:
        print("connecting")
        return "session-42"

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.active = False
        print("disconnected")


class Count:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __enter__(self) -> int32:
        return self.v

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Cb:
    f: Callable[[], int32]

    def __init__(self, f: Callable[[], int32]) -> None:
        self.f = f

    def __enter__(self) -> int32:
        return self.f() + 100

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def test_basic() -> None:
    with Logger("A") as a:
        a.log("hello")
    print("after with")


def test_no_as() -> None:
    with Logger("B"):
        print("inside B")
    print("after B")


def test_enter_returns_different_type() -> None:
    with Connection() as session:
        print(session)
    print("after connection")


def test_multiple_ctx_managers() -> None:
    with Logger("X") as x, Logger("Y") as y:
        x.log("first")
        y.log("second")
    print("after both")


def test_variable_visible_after() -> None:
    with Logger("V") as v:
        v.log("inside")
    v.log("after")


def early_return_helper() -> str:
    with Logger("R") as r:
        r.log("before return")
        return "result"


def test_early_return() -> None:
    """__exit__ must fire even when returning from inside the with block."""
    val = early_return_helper()
    print(val)


def nested_with_all_return(flag: bool) -> str:
    """Nested `with` where every path through the body returns: exercises the
    body_terminates=True + outer-finally branch in _gen_finally_body_and_epilogue
    (inner with's normal-path label gotos outer's finally label without the
    `if (retval)` guard). Also covers the inner with-as target getting
    pre-declared as std::optional<T> by the outer's _emit_branch_decls --
    the inner _gen_with must assign to the slot, not declare a shadowing local."""
    with Logger("OUT") as nw_outer:
        with Logger("IN") as nw_inner:
            nw_outer.log("outer ctx")
            nw_inner.log("inner ctx")
            if flag:
                return "yes"
            return "no"


def test_nested_with_all_return() -> None:
    print(nested_with_all_return(True))
    print(nested_with_all_return(False))


def test_body_var_survives_scope() -> None:
    """Variables declared inside with body must be visible after the block."""
    with Logger("S") as s:
        x = 10
        y = "hello"
    print(x)
    print(y)


def test_body_record_var_survives_scope() -> None:
    """Non-value type declared in body exercises pointer-local pre-decl path."""
    with Logger("T") as t:
        inner = Logger("inner")
        inner.log("inside")
    inner.log("after")


def test_reuse_with_var_name() -> None:
    with Logger("V1") as v:
        v.log("first")
    with Logger("V2") as v:
        v.log("second")
    v.log("after reuse")


def test_exception_in_body() -> None:
    """__exit__ must fire when an exception escapes the with body."""
    try:
        with Logger("E") as e:
            e.log("before throw")
            raise ValueError("boom")
    except ValueError:
        print("caught")


def test_exception_multi() -> None:
    """Multiple managers: LIFO exit on exception path."""
    try:
        with Logger("M1") as m1, Logger("M2") as m2:
            m1.log("ok")
            raise ValueError("multi")
    except ValueError:
        print("caught multi")


def test_with_in_try_finally() -> None:
    """with nested inside try/finally -- both cleanups run."""
    try:
        with Logger("N"):
            print("inside with")
    finally:
        print("outer finally")


def test_break_in_with() -> None:
    """break inside with body in a loop -- __exit__ fires before exiting."""
    for i in range(5):
        with Logger("BK"):
            if i == 2:
                break
            print(i)
    print("after break loop")


def test_continue_in_with() -> None:
    """continue inside with body in a loop -- __exit__ fires before next iteration."""
    for i in range(5):
        with Logger("CT"):
            if i == 2:
                continue
            print(i)
    print("after continue loop")


def test_later_item_reads_target() -> None:
    # A later item's manager is a lambda reading an earlier item's target.
    with Count(7) as n, Cb(lambda: n) as result:  # tpyc: ok
        print("later_item:", n, result)


def comp_names_target(xs: list[int32]) -> int32:
    # A manager comprehension's variable has the item target's name.
    with Count(sum([n for n in xs])) as n:  # tpyc: ok
        return n


def later_comp_names_target(xs: list[int32]) -> int32:
    # A later item's manager comprehension has that item's target name and
    # reads an earlier target.
    with Count(10) as a, Count(sum([b * a for b in xs])) as b:  # tpyc: ok
        return a + b


def test_comp_names_target() -> None:
    print("comp_target:", comp_names_target([1, 2]))
    print("later_comp_target:", later_comp_names_target([1, 2]))


def test_comp_names_body_hoist(xs: list[int32]) -> None:
    # A body name predeclared before the with (read after it) is also a
    # manager comprehension's variable. A predeclared TARGET of that name
    # is BUGS.md#with-existing-scalar-target-rejected.
    with Count(sum([x for x in xs])) as n:  # tpyc: ok
        x = n + 1
    print("body_hoist:", x)


class Items:
    items: list[int]

    def __init__(self, v: int) -> None:
        self.items = [v]

    def __enter__(self) -> list[int]:
        return self.items

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def test_comp_names_ref_body_hoist(rows: list[int]) -> None:
    # A reference-type body name (a pointer predeclared before the manager)
    # is also a manager comprehension's variable; the alias is mutated after
    # the with.
    with Items(len([ys for ys in rows])) as xs:  # tpyc: ok
        ys = xs
    ys.append(42)
    print("hoist_ref:", ys, xs)


test_basic()
print("---")
test_no_as()
print("---")
test_enter_returns_different_type()
print("---")
test_multiple_ctx_managers()
print("---")
test_variable_visible_after()
print("---")
test_early_return()
print("---")
test_nested_with_all_return()
print("---")
test_body_var_survives_scope()
print("---")
test_body_record_var_survives_scope()
print("---")
test_reuse_with_var_name()
print("---")
test_exception_in_body()
print("---")
test_exception_multi()
print("---")
test_with_in_try_finally()
print("---")
test_break_in_with()
print("---")
test_continue_in_with()
print("---")
test_later_item_reads_target()
print("---")
test_comp_names_target()
test_comp_names_body_hoist([1, 2])
test_comp_names_ref_body_hoist([5, 6])
