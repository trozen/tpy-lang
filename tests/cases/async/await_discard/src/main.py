# Standalone `await sub()` -- result is discarded; sub runs for its
# side effects.
from tpy.extern import cpp_template

async def side_effect() -> None:
    print("inside-side-effect")

async def caller() -> None:
    await side_effect()
    print("after-await")

@cpp_template("(::tpyapp::main::caller().poll(::tpy::Waker{{}}).is_ready())")
def drive_caller() -> bool: ...

def main() -> None:
    if drive_caller():
        print("done")

main()
