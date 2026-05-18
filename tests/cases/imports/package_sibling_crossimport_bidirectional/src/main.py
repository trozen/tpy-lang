# Bidirectional variant of package_sibling_crossimport: pkg.a and
# pkg.b mutually cross-import (Python-level cycle). Exercises the
# descendant-submodule suppression under the cycle-peer codegen path
# (records get `<peer>_fwd.hpp` includes and out-of-line method
# bodies; the new sibling-fwd-decls in pkg.hpp must coexist with that
# infrastructure).
from tpy import Int32
from pkg import A, B, with_b


def main() -> None:
    a = A()
    b = B()
    print(a.kind() + b.kind())
    print(with_b())


main()
