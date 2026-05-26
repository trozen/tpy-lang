# with-statement generators on the resumable frame: yield inside a with body.
# Verifies __enter__/__exit__ run correctly around suspension points.
from typing import Iterator

class Tracer:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> None:
        print("enter", self.label)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit", self.label)

def gen_with_yield(xs: list[int]) -> Iterator[int]:
    with Tracer("g"):
        for x in xs:
            yield x

def main():
    for v in gen_with_yield([10, 20, 30]):
        print(v)

main()
