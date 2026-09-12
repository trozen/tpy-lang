# The str source's sibling: a BYTES genexpr source keeps rejecting -- its
# element read has no admitted loop-var binding.
from tpy import int32


def main() -> None:
    data = b"abc"
    d = dict(((b, 0.0) for b in data))  # tpyc: error(/genexpr.iterable_shape/)
    print(len(d))


main()
