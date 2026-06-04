# Forwarding requires the alias be bound directly from a protocol PARAMETER.
# An alias of an alias (`ys = xs`, where `xs` is itself a forwarded alias) is
# sourced from a local, not the param, so `ys` has no concrete frame backing
# and is rejected cleanly -- guards the non-param-source branch of the gate.
# Bind directly from the parameter (`ys = it`) instead; see
# gen_proto_param_aliased_local.
from typing import Iterator, Iterable


def echo(it: Iterable[int]) -> Iterator[int]:  # tpyc: error(/protocol type aliasing a protocol-typed parameter/)
    xs = it
    ys = xs
    for x in ys:
        yield x
        yield x


def main() -> None:
    for v in echo([1, 2]):
        print(v)


main()
