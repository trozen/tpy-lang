# Error: with statement on a type that has no __enter__/__exit__
from tpy import Int32


class Plain:
    x: Int32


def main() -> None:
    with Plain() as p:  # tpyc: error(/missing __enter__/)
        pass

main()
