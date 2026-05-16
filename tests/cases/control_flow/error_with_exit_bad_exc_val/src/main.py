# __exit__ exc_val must be `None` or `Optional[BaseException]`. Other types
# are rejected so codegen's call-site shape (passing &__exc / nullptr /
# monostate{}) remains well-defined.


class Bad:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val: int, exc_tb) -> None:  # tpyc: error(/__exit__ exc_val must be None or Optional\[BaseException\]/)
        pass


def main() -> None:
    pass


main()
