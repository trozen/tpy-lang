# print(*gen()) is refused: print walks a sequence in place, and walking a
# generator while printing would interleave its side effects with the output
# (CPython drains it first). The message names the list(...) spelling
# (docs/LANGUAGE_FEATURES.md, the print bullets of the builtins section).
from typing import Iterator


def countdown(n: int) -> Iterator[int]:
    while n > 0:
        yield n
        n -= 1


def main() -> None:
    print(*countdown(3))  # tpyc: error(/print\(\) can unpack only a list, Array or Span.*print\(\*list\(\.\.\.\)\)/)


main()
