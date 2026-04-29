# Class constants on a *nested* record imported from another module.
# Codegen must (a) qualify the dotted owner name `Limits.Inner` to
# `::tpyapp::limits::Limits::Inner`, and (b) skip the side-effects wrapper
# since the receiver `Limits.Inner` is a static type reference, not a
# value expression.
from limits import Limits


def main() -> None:
    print(Limits.Inner.MAX)
    print(Limits.Inner.TAG)


main()
