# async generators (yield in async def) -- rejected as not-yet-supported in v1.
async def gen() -> int:  # tpyc: error(/async generators .* not yet supported/)
    yield 1
    yield 2

def main() -> None:
    print("ok")

main()
