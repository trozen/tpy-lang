# Regression: a class pattern's keyword capture binds the field's
# declared type. A subsequent `if v is not None:` inside the case body
# narrows v to the inner non-None type and the use site (e.g.
# `print("..." + v)`) must codegen with the deref applied.
# Before the fix, codegen treated the captured name as untyped and
# emitted str_concat with `std::optional<...>` directly, failing to
# compile.
from typing import Optional


class Build:
    target: Optional[str]
    jobs: Optional[str]
    def __init__(self, target: Optional[str], jobs: Optional[str]) -> None:
        self.target = target
        self.jobs = jobs


class Test:
    filter_: Optional[str]
    def __init__(self, filter_: Optional[str]) -> None:
        self.filter_ = filter_


def describe(s: Build | Test) -> None:
    match s:
        case Build(target=t, jobs=j):
            if t is not None:
                print("target=" + t)
            if j is not None:
                print("jobs=" + j)
        case Test(filter_=f):
            if f is not None:
                print("filter=" + f)


def main() -> None:
    describe(Build("release", "4"))
    describe(Build(None, "1"))
    describe(Test("smoke"))
    describe(Test(None))


main()
