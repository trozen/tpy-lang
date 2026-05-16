# __exit__ must return bool or None; other return types are rejected.


class Bad:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> int:  # tpyc: error(/__exit__ must return bool or None/)
        return 0


def main() -> None:
    pass


main()
