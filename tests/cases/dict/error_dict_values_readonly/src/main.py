# Mutation through values() of a readonly dict is rejected in sema (the
# const view yields readonly elements).
from tpy import Int32
from tpy import readonly


def bad_values(d: readonly[dict[str, list[Int32]]]):
    for v in d.values():
        v.append(9)  # tpyc: error(/non-readonly method 'append' on readonly/)


def main():
    d: dict[str, list[Int32]] = {}
    d["a"] = [1]
    bad_values(d)


main()
