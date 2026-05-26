# with-statement generator where an exception is raised inside the `with`
# body AFTER a yield resumes. Verifies the resumable __exit__ emit passes the
# live exception (a non-null exc_val carrying the raised value), not the
# normal-exit `{}, nullptr, {}` args -- the suppressing __exit__ both sees the
# message and swallows the exception, so the generator continues past the with.
from typing import Iterator


class Suppressor:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print(self.name, "suppressing", str(exc_val))
            return True
        return False


def gen() -> Iterator[int]:
    with Suppressor("S") as s:
        yield 1
        raise ValueError(s)
    yield 99


def main() -> None:
    for v in gen():
        print(v)


main()
