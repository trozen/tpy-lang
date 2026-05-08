# async + @export is not supported in v1 because exported coroutines need ABI design.
from tpy.extern import export


@export(binding="C")
async def f() -> int:  # tpyc: error(/async def \+ @export .* not yet supported/)
    return 42


def main() -> None:
    print("ok")


main()
