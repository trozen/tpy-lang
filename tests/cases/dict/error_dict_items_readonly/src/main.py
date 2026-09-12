# Mutation through items() of a readonly dict is rejected in sema (the
# const view yields readonly elements).
from tpy import int32
from tpy import readonly


def bad_items(d: readonly[dict[str, list[int32]]]):
    for k, v in d.items():
        v.append(9)  # tpyc: error(/non-readonly method 'append' on readonly/)


def main():
    d: dict[str, list[int32]] = {}
    d["a"] = [1]
    bad_items(d)


main()
