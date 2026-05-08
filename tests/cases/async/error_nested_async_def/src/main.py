# Nested async def is not yet supported in v1. async def is allowed at module level.
def outer() -> None:
    async def inner() -> int:  # tpyc: error(/nested async def is not yet supported/)
        return 42
    print("ok")

outer()
