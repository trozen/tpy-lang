# A coroutine frame copies its `str` parameter: a view of it dies with the
# frame, before the awaiting caller reads it.
# (docs/LANGUAGE_FEATURES.md: a parameter the function holds its own copy of).
import asyncio
from tpy import StrView


async def view(s: str) -> StrView:
    return s  # tpyc: error(/Cannot return StrView referencing parameter 's' \(the function's own copy\)/)


def main() -> None:
    print(asyncio.run(view("n" * 30))[:2])


main()
