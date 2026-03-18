# @native class inheriting from non-native class should error
from tpy.extern import native

class Foo:
    pass

@native("CppBar")
class Bar(Foo): ...  # tpyc: error(/can only inherit from other @native/)
