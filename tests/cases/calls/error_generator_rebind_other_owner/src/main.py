# Same-named generator methods of two classes are not variants of one set:
# rebinding a name from one's generator to the other's takes the ordinary
# "different generator" refusal (one name holds one generator function's
# objects), not the variant-set one: the documented restriction of
# docs/LANGUAGE_FEATURES.md "Generators", BUGS.md#generator-rebind-rejects.
from typing import Iterator
from tpy import int32


class A:
    def gen(self) -> Iterator[int32]:
        yield 1


class B:
    def gen(self) -> Iterator[int32]:
        yield 2


def main() -> None:
    g = A().gen()
    for v in g:
        print(v)
    g = B().gen()  # tpyc: error(/cannot rebind 'g' to a different generator/)
    for v in g:
        print(v)


main()
