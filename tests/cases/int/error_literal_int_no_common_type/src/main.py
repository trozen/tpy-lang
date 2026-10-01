# List literal peers of fixed widths no widening joins (int32 and uint32): the
# error advises converting to a type holding both, never a union annotation
# (LANGUAGE_FEATURES "Mixed widths at a generic type parameter, `max` / `min`
# and container literals").
from tpy import int32, uint32


def main() -> None:
    a = int32(-1)
    u = uint32(4000000000)
    xs = [a, u]  # tpyc: error(/convert to one type, e.g. int64\(\.\.\.\) on each element/)
    print(xs)


main()
