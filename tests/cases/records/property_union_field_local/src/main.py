# Regression: a value-variant union field read inside a const
# `@property` body needs the const conversion (`to_const_ptr_variant`).
# Before the fix, codegen for the auto_readonly-cloned const overload
# didn't include `self` in `const_ref_params`, so the field-read path
# picked the non-const `to_ptr_variant`, leaving `to_ptr_variant(this->f)`
# on a `const std::variant<...>` -- a cv-qualifier mismatch at the C++
# layer.
from typing import Optional


class A:
    target: Optional[str]
    def __init__(self, target: Optional[str]) -> None:
        self.target = target


class B:
    filter_: Optional[str]
    def __init__(self, filter_: Optional[str]) -> None:
        self.filter_ = filter_


class Holder:
    sub: A | B
    def __init__(self, sub: A | B) -> None:
        self.sub = sub

    @property
    def label(self) -> str:
        v = self.sub
        match v:
            case A(target=_):
                return "a"
            case B(filter_=_):
                return "b"


def main() -> None:
    print(Holder(A("rel")).label)
    print(Holder(B("smoke")).label)


main()
