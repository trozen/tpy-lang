# The from-import of a name `ua` only copies later is warned.
# `import uc` only gives the C++ header its include of `uc`
# (BUGS.md#cycle-reexported-global-missing-include).
import uc  # noqa: F401
from ua import value  # tpyc: warning(/'value' is imported from 'ua' while 'ua' is still being imported -- .* before it binds 'value' \(ua\.py:3\); CPython raises ImportError.*\. Move the import at ua\.py:3 above that import/)


def f() -> int:
    return value
