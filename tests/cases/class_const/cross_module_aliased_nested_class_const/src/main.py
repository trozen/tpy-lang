# Aliased cross-module nested-class const access. `L.Inner.MAX` resolves
# through the alias to `Limits.Inner.MAX` and emits the canonical
# `::tpyapp::limits::Limits::Inner::MAX`. Verifies the import handler
# registers nested types under both the canonical and alias-prefixed
# keys when an alias is used.
from limits import Limits as L


def main() -> None:
    print(L.Inner.MAX)
    print(L.Inner.TAG)


main()
