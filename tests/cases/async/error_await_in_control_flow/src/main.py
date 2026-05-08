# `await` inside an if/while/for/with/try sub-body is not yet supported
# in v1. The await-lift pre-pass handles awaits in condition/header
# positions (e.g. `if await cond():`) but not awaits inside loop bodies
# or branch bodies, which need per-suspension try-stack codegen
# (deferred to v1.5).
import asyncio

async def sub() -> None:
    pass

async def caller() -> None:
    for i in range(3):  # tpyc: error(/await inside an if\/while\/for\/with\/try sub-body/)
        await sub()

def main() -> None:
    asyncio.run(caller())

main()
