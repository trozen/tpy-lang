# Pins the inner-finally-on-throw path: when an except handler body
# raises a new exception, the enclosing try's finally must run before
# the new exception propagates to an outer try's handler. Python
# semantics require: inner-handler -> inner-finally -> outer-handler
# -> outer-finally. The CFG-lite codegen wraps handler bodies in a
# nested try/catch that calls the parent finally on throw and uses
# the handler entry BB's region_stack (not the case-entry's) to
# populate the active finally chain.
import asyncio
from tpy import int32

async def fail_value() -> int32:
    raise ValueError("inner-fail")

async def go() -> int32:
    try:
        try:
            x = await fail_value()
            return x
        except ValueError:
            print("inner-handler")
            raise RuntimeError("handler-raised")
        finally:
            print("inner-finally")
    except RuntimeError:
        print("outer-handler")
        return int32(42)
    finally:
        print("outer-finally")

def main() -> None:
    print(asyncio.run(go()))

main()
