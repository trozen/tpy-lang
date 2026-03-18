# Error: with statement on type missing __enter__

class NoEnter:
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # tpyc: ok
        pass


def main() -> None:
    with NoEnter() as n:  # tpyc: error(/missing __enter__/)
        pass

main()
