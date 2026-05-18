# `await` inside a sync `for` body is deferred to a follow-up
# milestone. The general await-in-control-flow lift (v1.5 M3) covers
# if / while / try, but a sync for needs an iter/next desugaring
# pass (planned). The narrower diagnostic surfaces from the CFG
# builder at codegen time.
import asyncio

async def sub() -> None:
    pass

async def caller() -> None:
    for i in range(3):  # tpyc: error(/for. body needs the for-loop desugaring/)
        await sub()

def main() -> None:
    asyncio.run(caller())

main()
