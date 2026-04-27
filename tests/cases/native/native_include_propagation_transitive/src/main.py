# Test that # tpy: include() of a native module transitively reached
# through a field/method chain is emitted into the consumer header.
# main.py only imports `s`, but `S.outer` returns A from module `a`, and
# `a.inner` is itself an A field of type B. Accessing `s.outer.inner.flag`
# requires the complete A and B types -- their definitions live in
# <x/a.hpp>, which `s.py`'s hand-written <x/s.hpp> only forward-declares.
# Pre-fix: consumer hpp emitted only <x/s.hpp>, so the field access broke
# at C++ compile time. Post-fix: reach analysis follows S.outer's return
# type to module a and emits <x/a.hpp> directly.
from s import S
from tpy import Ptr

def f(s: Ptr[S]) -> bool:
    return s.outer.inner.flag  # tpyc: nullable(s)

def main() -> None:
    pass

main()
