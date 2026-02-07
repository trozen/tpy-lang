from tpy import Int32

class A:
    a: Int32

class B:
    b: Int32

class C(A, B):  # tpyc: error(/Multiple class inheritance/)
    c: Int32
