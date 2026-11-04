# An `__aexit__` @dispatch set is refused at registration, as an `__exit__`
# set is: CPython picks the variant from the exception the block raised,
# while the one exit call `async with` makes serves every way out of the
# block (documented restriction, docs/LANGUAGE_FEATURES.md "Overloading";
# lifting it needs a variant per exit path, BUGS.md#aexit-lacks-exit-checks).
import asyncio
from tpy import dispatch


class Manager:
    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> "Manager":
        return self

    @dispatch
    async def __aexit__(self, kind: None, value: BaseException, tb: None) -> bool:  # tpyc: error(/__aexit__ cannot be overloaded/)
        return True

    @dispatch
    async def __aexit__(self, kind: None, value: None, tb: None) -> bool:
        return False


async def amain() -> None:
    m = Manager()
    async with m:
        print("body")


asyncio.run(amain())
