# Error: @readonly_alt on methods with method-level type parameters is not yet supported.
# Applies to both unbounded (def f[U]) and bounded (def f[U: Default]) params.
# This is a sema limitation (not a design constraint) -- could be lifted in the future.
from tpy import readonly_alt, Default

class Container:
    @readonly_alt  # tpyc: error(/not yet supported/)
    def find[U](self, value: U) -> U: ...
    # Same error fires for bounded params: def make[U: Default](self) -> U

def main() -> None:
    pass
