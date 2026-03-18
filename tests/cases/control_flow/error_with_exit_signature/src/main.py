# Error: __exit__ must have 3 params (CPython protocol)

class BadExit:
    def __enter__(self) -> str:
        return "x"

    def __exit__(self) -> None:  # tpyc: error(/__exit__ must have 3 parameters/)
        pass
