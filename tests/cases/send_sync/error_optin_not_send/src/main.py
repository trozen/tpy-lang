# class Foo(Send) is a checked claim: structural derivation must agree.
from tpy import Int32, Ptr, Send

class Order(Send):  # tpyc: error(/declares Send but field 'h: Ptr\[Int32\]' is not Send/)
    h: Ptr[Int32]
