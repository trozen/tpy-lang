# Phase 20 sema rule: the Throwable ABI methods (clone/__raise__/what)
# are codegen-emitted automatically on every Throwable subclass; a user
# override would collide with the auto-emit at C++ compile time. Sema
# rejects at the definition site so the user gets a targeted message.
class CustomError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)

    def __raise__(self) -> None:  # tpyc: error(/cannot define '__raise__'/)
        pass


def main() -> None:
    pass


main()
