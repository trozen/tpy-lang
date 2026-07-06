# @virtual_raise marks a hand-written dispatching C++ __raise__, which only
# an @native class can supply; on a plain TPy class it would be accepted-
# then-meaningless (user __raise__ bodies are rejected), so sema errors.
from tpy.extern import virtual_raise


@virtual_raise
class AppError(Exception):  # tpyc: error(/@virtual_raise on 'AppError' requires @native/)
    def __init__(self, message: str) -> None:
        self.message = message


def main() -> None:
    raise AppError("boom")


main()
