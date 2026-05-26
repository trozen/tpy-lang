# try/finally generators on the resumable frame: helper-based finally
# (no yield in finally body). Tests that cleanup runs on normal and early exit.
from typing import Iterator

def counted(xs: list[int]) -> Iterator[int]:
    for x in xs:
        try:
            if x == 99:
                return
            yield x
        finally:
            print("done", x)

def main():
    for v in counted([1, 2, 99, 3]):
        print(v)

main()
