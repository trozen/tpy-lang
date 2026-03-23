# Warning: mutation during simple generator iteration (single yield, lambda path)
from tpy import Int32
from typing import Iterator

def doubled(items: list[Int32]) -> Iterator[Int32]:
    for x in items:
        yield x * 2

def main():
    items: list[Int32] = [1, 2, 3]
    for x in doubled(items):
        print(x)
        items.append(42)  # tpyc: warning(/Mutation of 'items' while iterating/)
        break

main()
