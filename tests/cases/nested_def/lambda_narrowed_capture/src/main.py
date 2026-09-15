# A lambda capturing an isinstance-NARROWED union subject: the entry names the
# extraction alias, not the subject. The two lanes differ by what the entry
# would copy, so each is covered here; the escaping lane over a REFERENCE-typed
# member rejects instead (nested_def/error_lambda_narrowed_escaping_capture).
from typing import Callable

from tpy import Fn, float64, int32


class A:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class B:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


def mutate_then_call(a: A, f: Fn[[], int32]) -> int32:
    # the caller built `f` before this call, so this mutation lands AFTER the
    # capture was taken and before the closure runs
    a.a = 99
    return f()


def hold(f: Callable[[], int32]) -> int32:
    return f()


def fn_record_position(x: A | B) -> None:
    if isinstance(x, A):
        # non-escaping lane: the entry is `&<alias>`, so the closure reads the
        # subject the callee mutated -- 99, where a snapshot would print 1
        print("fn_record:", mutate_then_call(x, lambda: x.a))  # tpyc: ok


# discriminates on float64 (== float under CPython) so the literal argument
# narrows the same on both runtimes
def escaping_value_position(v: float64 | str) -> None:
    if isinstance(v, float64):
        # escaping lane over a VALUE-typed member: the snapshot is what the
        # un-narrowed union capture would take too, so the entry stays
        print("escaping_value:", hold(lambda: int32(v) + 1))  # tpyc: ok


def main() -> None:
    fn_record_position(A(1))
    escaping_value_position(2.5)


main()
