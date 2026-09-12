# class Foo(Send) is a checked claim: structural derivation must agree.
from tpy import int32, Ptr, Send

class Order(Send):  # tpyc: error(/declares Send but field 'h: Ptr\[int32\]' is not Send/)
    h: Ptr[int32]
