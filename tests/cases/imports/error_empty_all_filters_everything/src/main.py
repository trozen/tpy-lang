# Consumer-side guard for `__all__ = []`: lib defines a function but
# its empty `__all__` exports nothing, so `from lib import *` brings
# no names into scope. Explicit `from lib import name` is still
# allowed (matches CPython -- __all__ is the star filter, not a
# visibility wall).
from lib import *
from lib import hidden_via_empty_all as direct_access

direct_access()
hidden_via_empty_all()  # tpyc: error(/hidden_via_empty_all/)
