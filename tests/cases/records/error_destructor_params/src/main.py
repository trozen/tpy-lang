# Tests error: __del__ must not have parameters

class Foo:
    def __del__(self, x: int):  # tpyc: error(/__del__.*must not have parameters/)
        pass
