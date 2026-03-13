# Error: @auto_readonly on methods with method-level type parameters is not yet supported.
# Applies to both unbounded (def f[U]) and bounded (def f[U: Default]) params.
# This is a sema limitation (not a design constraint) -- could be lifted in the future.
from tpy import auto_readonly, Default

class Container:
    @auto_readonly  # tpyc: error(/not yet supported/)
    def find[U](self, value: U) -> U: ...
    # Same error fires for bounded params: def make[U: Default](self) -> U

def main() -> None:
    pass
