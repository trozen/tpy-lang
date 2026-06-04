# A static-protocol parameter aliased into a local (`xs = it`) then iterated
# across yields. The alias is a compile-time forwarding to the captured param
# (it has no frame field of its own), so it reuses the param's deduced template
# arg T_it. Like the direct-iteration case, the generator BORROWS the iterable:
# mutating the source after the generator is created but before lazy iteration
# starts is observed (a copy would miss the appended element).
from typing import Iterator, Iterable


def echo(it: Iterable[int]) -> Iterator[int]:
    xs = it
    for x in xs:
        yield x
        yield x


def main() -> None:
    data = [1, 2]
    g = echo(data)
    data.append(3)  # tpyc: warning(/while borrowed/)
    for v in g:
        print(v)


main()
