# Tests error: __del__ must return None

class Foo:
    def __del__(self) -> int:  # tpyc: error(/__del__.*must return None/)
        return 0
