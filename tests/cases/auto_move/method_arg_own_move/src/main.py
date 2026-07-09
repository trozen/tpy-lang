# Forwarding an Own[T] param into an Own[T] user-record method slot moves it at
# last use (`s.absorb(std::move(p))`); @nocopy Payload forbids a silent copy.
from tpy import Int32, Own, nocopy


@nocopy
class Payload:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Sink:
    total: Int32

    def __init__(self) -> None:
        self.total = 0

    def absorb(self, p: Own[Payload]) -> None:
        self.total += p.n


def forward(s: Sink, p: Own[Payload]) -> None:
    # p is an Own[Payload] param at its last use -> the method call moves it.
    s.absorb(p)


def main() -> None:
    s = Sink()
    forward(s, Payload(5))
    forward(s, Payload(7))
    print(s.total)


main()
