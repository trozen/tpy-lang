# `await` inside a `for ... else:` clause is rejected -- the CFG
# builder doesn't yet model the break-vs-normal-exit distinction
# that the else clause depends on.
import asyncio


async def sub() -> None:
    pass


async def caller() -> None:
    for i in range(3):  # tpyc: error(/`for`.`else:`.*planned follow-up/)
        pass
    else:
        await sub()


def main() -> None:
    asyncio.run(caller())


main()
