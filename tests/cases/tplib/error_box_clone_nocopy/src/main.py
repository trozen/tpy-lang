# Box[T].clone() copies T at the call site (Box(self.get())), so it's gated
# on the T: Copyable shadow bound. Box wrapping a @nocopy payload (Rc here)
# must surface a clean sema diagnostic rather than the cryptic C++ template
# error that this used to produce.
from tpy import Int32
from tplib import Box, Rc


def main() -> None:
    a = Box(Rc.new(Int32(7)))
    b = a.clone()  # tpyc: error(/Method 'clone' requires type parameter 'T' to satisfy 'Copyable'/)
    print(b.get().get())


main()
