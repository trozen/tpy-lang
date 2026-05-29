# Only an init-only Optional[@dynamic protocol] local is allowed (it backs the
# const Pet* with the materialized rvalue). A no-init declaration has no rvalue
# to point at, so it stays rejected -- the abstract base has no value-repr for
# an uninitialized slot. (Init-only is covered by opt_dyn_protocol_local.)
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


def f(cond: bool) -> str:
    p: Optional[Pet]             # tpyc: error(/only supported at a parameter position/)
    if cond:
        p = Dog()
    else:
        p = Dog()
    return p.name()
