# Tests error: __del__ cannot be a static method

class Foo:
    @staticmethod
    def __del__():  # tpyc: error(/__del__.*cannot be a static method/)
        pass
