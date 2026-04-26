# Inheriting from a non-generic protocol with spurious type arguments is
# rejected at sema with a specific arity diagnostic. Distinct from the
# resolver-side "requires type arguments" error -- this is the inverse
# (parent has no type params, child supplies some).
from typing import Sized, Protocol


class Child(Sized[int], Protocol):  # tpyc: error(/inherits from non-generic protocol/)
    pass
