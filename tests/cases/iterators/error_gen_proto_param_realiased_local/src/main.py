# A protocol-param alias is only forwarded when it is bound exactly once from a
# bare param (a compile-time alias). A REASSIGNED local is not a pure forward,
# so it has no concrete frame backing and is rejected cleanly at frame emit --
# guards the single-assignment gate. The single-assignment form (one `xs = it`)
# is supported; see gen_proto_param_aliased_local.
from typing import Iterator, Iterable


def echo(it: Iterable[int], it2: Iterable[int]) -> Iterator[int]:  # tpyc: error(/protocol type aliasing a protocol-typed parameter/)
    xs = it
    xs = it2
    for x in xs:
        yield x
        yield x


def main() -> None:
    for v in echo([1, 2], [3, 4]):
        print(v)


main()
