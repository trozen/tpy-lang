# Regression: `from pkg import sub` binds the submodule as a namespace, then
# `sub.Class.method()` and `sub.Class.CONST` resolve through it. The binding
# has a dotted `import_source` (e.g. ("pkg.models", "pkg.models")), different
# from `import m` and `import m as alias`; covers a separate _resolve_module_name
# code path.
from pkg import models


def main() -> None:
    w = models.Widget.make(21)  # tpyc: ok
    print(w.value)
    print(models.Widget.SIZE)  # tpyc: ok


main()
