# __exit__ cannot be overloaded -- a single codegen call site per `with`
# item can't dispatch between overloads with differing return / exc_val
# shapes. Sema rejects up front.

from typing import overload, Optional


class Bad:
    def __enter__(self) -> int:
        return 1

    @overload
    def __exit__(self, exc_type, exc_val: None, exc_tb) -> None: ...  # tpyc: error(/__exit__ cannot be overloaded/)
    @overload
    def __exit__(self, exc_type, exc_val: Optional[BaseException], exc_tb) -> bool: ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False


def main() -> None:
    pass


main()
