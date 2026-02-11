from tpy import readonly
import helpers as h


@readonly
def bad() -> None:
    h.mutate()  # tpyc: error(/Call to non-readonly or unknown-effect .*'mutate'/)


bad()
