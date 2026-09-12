# Diamond inheritance is rejected in D22 v1. Non-virtual C++ MI would duplicate
# the shared ancestor's subobject; users who need runtime polymorphism should
# make the shared ancestor a @dynamic protocol.
from tpy import int32


class A:
    x: int32


class B(A):
    pass


class C(A):
    pass


class D(B, C):  # tpyc: error(/Diamond inheritance not supported.*'A'.*'B'.*'C'/)
    pass
