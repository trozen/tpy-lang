# Error: for...else with yield in generator
from tpy import Int32
from typing import Iterator

def gen(items: list[Int32]) -> Iterator[Int32]:
    yield -1
    for x in items:  # tpyc: error(/for\.\.\.else with yield/)
        yield x
    else:
        pass

def main():
    pass

main()
