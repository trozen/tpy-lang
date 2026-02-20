# Tests error: __del__ cannot have type parameters

class Foo:
    def __del__[T](self):  # tpyc: error(/__del__.*cannot have type parameters/)
        pass
