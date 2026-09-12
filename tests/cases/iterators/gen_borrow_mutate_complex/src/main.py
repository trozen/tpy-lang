# Warning: mutation of source container during generator iteration
from tpy import int32
from typing import Iterator

def doubled(items: list[int32]) -> Iterator[int32]:
    yield -1
    for x in items:
        yield x * 2

def main():
    items: list[int32] = [1, 2, 3]
    for x in doubled(items):
        print(x)
        items.append(42)  # tpyc: warning(/Mutation of 'items' while iterating/)
        break  # break immediately so the mutation doesn't corrupt the iterator

main()
