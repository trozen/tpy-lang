# async + @error_return: mutually exclusive in v1 (await foo()? not yet designed).
from tpy import error_return, ReturnException

class Err(Exception, ReturnException):
    pass

@error_return(Err)
async def f() -> int:  # tpyc: error(/async def \+ @error_return .* not yet supported/)
    return 42

def main() -> None:
    print("ok")

main()
