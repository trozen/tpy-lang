# `is` between two Rc payloads of a value type: the payload has no identity,
# so the test is rejected (stricter by design: docs/LANGUAGE_FEATURES.md,
# Operators / Comparison; the cell question is
# BUGS.md#rc-value-payload-cell-identity); Rc handles and reference payloads
# compare by identity (operators/identity_is).
from tplib import Rc


def main() -> None:
    a = Rc.new(10)
    b = a.clone()
    print(a.get() is b.get())  # tpyc: error(/'int32' is a value type and has no identity; use '=='/)


main()
