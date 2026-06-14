# Mutation through items() of a readonly dict is rejected in sema (the
# const view yields readonly elements).
from tpy import Int32
from tpy import readonly


def bad_items(d: readonly[dict[str, list[Int32]]]):
    for k, v in d.items():
        v.append(9)  # tpyc: error(/non-readonly method 'append' on readonly/)


def main():
    d: dict[str, list[Int32]] = {}
    d["a"] = [1]
    bad_items(d)


main()
