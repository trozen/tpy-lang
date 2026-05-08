# Auto-propagation through try-with-finally: finally must run before propagating
# the unexpected return out of the @error_return function.
from tpy import Int32, error_return, ReturnException

class MyErr(Exception, ReturnException):
    pass

@error_return(MyErr)
def fallible(x: Int32) -> Int32:
    if x < 0:
        raise MyErr
    return x * 2

@error_return(MyErr)
def caller(x: Int32) -> Int32:
    """Propagate-out: finally runs even when the call errors."""
    try:
        y = fallible(x)
        return y
    finally:
        print("cleanup-1")

@error_return(MyErr)
def caller_assign(x: Int32) -> Int32:
    """Propagate-out via assignment to existing var."""
    z: Int32 = 0
    try:
        z = fallible(x)
        return z
    finally:
        print("cleanup-2")

@error_return(MyErr)
def caller_stmt(x: Int32) -> None:
    """Propagate-out via statement-level call (result discarded)."""
    try:
        fallible(x)
    finally:
        print("cleanup-3")

def main() -> None:
    try:
        v = caller(-1)
    except MyErr:
        print("error-1")
    else:
        print(v)

    try:
        v = caller(3)
    except MyErr:
        print("error-1")
    else:
        print(v)

    try:
        v = caller_assign(-1)
    except MyErr:
        print("error-2")
    else:
        print(v)

    try:
        caller_stmt(-1)
    except MyErr:
        print("error-3")

main()
