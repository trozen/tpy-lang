# Error: protocol-typed-param generators (non-simple) are deferred. The
# struct becomes a template (T_it), but the resumable for-loop frame field
# would be typed against the abstract protocol (Iterable<int>, a C++
# concept) rather than the deduced template param. Until that substitution
# lands (TODO.md "Generator: protocol-typed params"), the eligibility gate
# rejects them with a clean diagnostic instead of a broken C++ build.
from typing import Iterator, Iterable


def echo(it: Iterable[int]) -> Iterator[int]:  # tpyc: error(/protocol-typed parameter are not yet supported/)
    for x in it:
        yield x
        yield x


def main() -> None:
    for v in echo([1, 2]):
        print(v)


main()
