# async + @noalloc: not yet supported in v1 (frame allocation control deferred).
from tpy import noalloc

@noalloc
async def f() -> int:  # tpyc: error(/async def \+ @noalloc .* not yet supported/)
    return 42

def main() -> None:
    print("ok")

main()
