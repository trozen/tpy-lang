# Walrus reassignment of an Optional-of-value local to a type incompatible with
# the union (str vs int32 | None) is rejected, exercising the coerce path on a
# UnionType existing type rather than a plain scalar.
from tpy import int32

def main() -> None:
    x: int32 | None = 5
    if (x := "oops"):  # tpyc: error(/reassignment to 'x'/)
        print(x)

main()
