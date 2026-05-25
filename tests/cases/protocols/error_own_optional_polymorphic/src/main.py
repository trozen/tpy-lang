# Own[Optional[Polymorphic]] is rejected at sema: an Own-Optional slot of
# a polymorphic class is laid out for the base alone, so storing a derived
# class would slice the dynamic type, and isinstance dispatch on it has no
# valid lowering today. Rejection is centralised in
# `tpyc/sema/type_ops.py::validate_type` so all positions (free-function
# params, return types, record fields, method params/returns) report the
# same diagnostic via the same code path; this test pins the diagnostic
# for the free-function param position.
from typing import Protocol, Optional
from tpy import dynamic, Own


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    def __init__(self) -> None:
        pass


def check(p: Own[Optional[Pet]]) -> bool:  # tpyc: error(/Own\[Optional\[Pet\]\]. is not yet supported/)
    return p is None


def main() -> None:
    pass


main()
