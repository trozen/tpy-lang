# async + @native is not supported in v1 because native declarations have no frame.
from tpy.extern import native


@native
async def f() -> int:  # tpyc: error(/async def \+ @native .* not yet supported/)
    ...


def main() -> None:
    print("ok")


main()
