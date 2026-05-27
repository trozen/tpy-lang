# A protocol TARGET is declined for bound coercion: a structural conformer
# satisfies `U: Pet` without inheriting Pet's C++ base, so a plain
# `Ptr[U] -> Ptr[Pet]` pointer upcast would be invalid. Such cases need the
# adapter path (out of scope), so the coercion is rejected here.
from typing import Protocol
from tpy import dynamic, Ptr


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


def as_pet[U: Pet](p: Ptr[U]) -> Ptr[Pet]:
    return p  # tpyc: error(/expected Ptr\[Pet\], got Ptr\[U\]/)
