# dict[K, V](x=...) with explicit type arguments gets the same dict-literal hint
# as dict(x=...) (BUGS.md#builtin-keyword-construction-rejects).
from tpy import int32


def main() -> None:
    d = dict[str, int32](x=1)  # tpyc: error(/keyword arguments to dict\(\) are not supported; write a dict literal/)
    print(d)


main()
