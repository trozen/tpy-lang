# User Exception subclass with no own __init__ inherits Exception(message).
# Plain `class E(Exception): pass` should accept E("...") just like Python.

class MyError(Exception):
    pass

# Multi-level pass-through: each link inherits Exception's __init__.
class Outer(MyError):
    pass

def main() -> None:
    try:
        raise MyError("first")
    except MyError:
        print("caught MyError")

    try:
        raise Outer("nested")
    except MyError:
        print("caught Outer as MyError")

    # Inherited __init__ also accepts the default empty message.
    try:
        raise MyError
    except MyError:
        print("bare")

main()
