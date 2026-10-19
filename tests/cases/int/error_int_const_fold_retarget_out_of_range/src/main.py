# An int literal compared with an Optional of a fixed width that cannot hold
# it is refused rather than converted to a different value
# (BUGS.md#optional-eq-literal-retarget-out-of-range).
from tpy import int64


def check(o: int64 | None) -> None:
    print(o != 2**63)  # tpyc: error(/literal.int_out_of_range/)


check(-2**63)
