# `await` inside a `while ... else:` clause is rejected -- the CFG
# builder doesn't yet model the break-vs-normal-exit distinction
# that the else clause depends on.
import asyncio


async def sub() -> None:
    pass


async def caller() -> None:
    i = 0
    while i < 3:  # tpyc: error(/`while`.`else:`.*planned follow-up/)
        i = i + 1
    else:
        await sub()


def main() -> None:
    asyncio.run(caller())


main()
