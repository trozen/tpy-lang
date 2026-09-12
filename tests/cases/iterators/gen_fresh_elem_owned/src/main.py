# A source whose __next__ builds a FRESH element each step is the opposite of
# gen_proto_param_ref_alias: the frame must OWN the loop element, not alias the
# step-result slot, or reading the leaked loop variable after the loop reads
# storage the exhausting advance has already reused.
from tpy import int32, Own
from typing import Iterator


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __next__(self) -> Own[Node]:
        if self.n >= 3:
            raise StopIteration()
        self.n += 1
        return Node(self.n)


class Fresh:
    def __iter__(self) -> Own[Counter]:
        return Counter()


def collect(src: Fresh) -> Iterator[int32]:
    for node in src:
        yield node.v
        yield node.v
    print("leaked loop var after the loop:", node.v)


def main() -> None:
    for got in collect(Fresh()):
        print(got)


main()
