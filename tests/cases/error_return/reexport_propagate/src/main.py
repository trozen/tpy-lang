# Re-export tolerance: `run` declares @error_return(AppError) where AppError is
# imported via the facade re-export, and propagates check() whose error type is
# the SAME AppError reached via its defining module. Both qualify to the
# defining-module qname, so the propagation must still match and compile.
from tpy import error_return, Int32
from facade import AppError, check

@error_return(AppError)
def run(n: Int32) -> Int32:
    return check(n)

def main() -> None:
    try:
        print(run(5))
        print(run(-1))
    except AppError:
        print("caught")

main()
