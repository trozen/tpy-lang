# An imported async module must receive its own coroutine runtime dependency.


async def compute(n: int) -> int:  # tpyc: ok
    return n


def loaded() -> str:
    return "loaded"
