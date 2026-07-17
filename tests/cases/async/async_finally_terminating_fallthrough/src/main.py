# An async try/except/finally with no suspension inside it is emitted by the
# sync statement path (the CFG only decomposes suspending try statements), so
# it shares the normal-path finally elision.
import asyncio


async def raise_from_finally() -> None:
    try:
        print("try body ran")
    except ValueError:
        print("unreachable handler")
    finally:
        print("finally ran")
        raise RuntimeError("from finally")


async def amain() -> None:
    try:
        await raise_from_finally()
    except RuntimeError:
        print("caught from finally")


def main() -> None:
    asyncio.run(amain())


main()
