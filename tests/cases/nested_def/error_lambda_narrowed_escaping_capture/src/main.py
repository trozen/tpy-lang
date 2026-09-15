# An ESCAPING (Callable) lambda capturing an isinstance-NARROWED union subject
# whose member is a reference type. The entry would name the extraction alias
# by value, snapshotting the record -- while the same lambda over the
# UN-narrowed subject captures the pointer variant and aliases, so the
# narrowing alone would flip alias into copy with no diagnostic. Rejected until
# the escaping lane can capture the subject and re-narrow inside
# (BUGS.md#escaping-capture-of-narrowed-subject). The non-escaping (Fn) lane
# binds the alias by reference and is fine: nested_def/lambda_narrowed_capture.
from typing import Callable

from tpy import int32


class A:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class B:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


def run(x: A | B) -> int32:
    if isinstance(x, A):
        # the escaping capture of the narrowed `x` is the subject
        f: Callable[[], int32] = lambda: x.a  # tpyc: error(/expr\.lambda/)
        x.a = 99
        return f()
    return 0


def main() -> None:
    print(run(A(1)))


main()
