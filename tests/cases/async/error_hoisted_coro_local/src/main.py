# A coroutine handle first bound inside a BRANCH: the decl is placed by the
# escape-hoist machinery, not the direct-init arm the rebind slot renders.
# `c = add_one(...)` assigned in both if/else arms still fails to compile.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


def main() -> None:
    flag = True
    if flag:
        c = add_one(41)
    else:
        c = add_one(1)  # tpyc: error(/decl\.rebind_source/)
    print(asyncio.run(c))


main()
