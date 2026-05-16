# __exit__ exc_type must be None in v1.5 (no traceback/type-object machinery).
# Any other annotation is rejected.


class Bad:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type: int, exc_val, exc_tb) -> None:  # tpyc: error(/__exit__ exc_type must be None/)
        pass


def main() -> None:
    pass


main()
