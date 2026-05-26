# try/except generators on the resumable frame: yield in body and handler.
from typing import Iterator

def guarded(xs: list[int]) -> Iterator[int]:
    for x in xs:
        try:
            if x < 0:
                raise ValueError("negative")
            yield x
        except ValueError:
            yield -1

def main():
    g = guarded([1, -2, 3, -4, 5])
    for v in g:
        print(v)

main()
