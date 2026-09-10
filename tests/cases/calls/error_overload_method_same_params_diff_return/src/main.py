# Method @dispatch variants with identical parameter types but
# different return types -- same constraint as free functions
# (C++ can't realise both). Auto-readonly / auto-own const-qualified
# variants are still permitted; this test only covers the duplicate
# case.
from tpy import Int32, dispatch


class C:
    @dispatch
    def f(self, x: Int32) -> Int32:
        return x * 2

    @dispatch
    def f(self, x: Int32) -> str:  # tpyc: error(/identical parameter types/)
        return str(x)


C().f(Int32(5))
