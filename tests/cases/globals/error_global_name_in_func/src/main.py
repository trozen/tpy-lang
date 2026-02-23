# Error: __name__ is a Final constant, cannot reassign at module level
__name__ = "custom"  # tpyc: error(/Cannot reassign Final/)

def f() -> None:
    print(__name__)

f()
