# value is bound in `wa` when `wb` runs.
# `import wc` only gives the C++ header its include of `wc`
# (BUGS.md#cycle-reexported-global-missing-include).
import wc  # noqa: F401
from wa import value  # tpyc: ok


def f() -> int:
    return value
