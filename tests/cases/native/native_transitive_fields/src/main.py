# Chained field access through intermediate record types that are NOT imported
# into the consumer. Only S is imported; A and Q are reached only via
# `self.s.a.q.flag`. Field lookup must resolve them via the cross-module record
# index, not require the consumer to re-import every intermediate type.
from native_stub import S
from tpy import Ptr
from tpy.extern import cpp_template

@cpp_template("get_s()")
def get_s() -> Ptr[S]: ...

class M:
    s: Ptr[S]
    def __init__(self, s: Ptr[S]) -> None:
        self.s = s
    def read(self) -> bool:
        return self.s.a.q.flag       # tpyc: ok
    def write(self) -> None:
        self.s.a.q.flag = True       # tpyc: ok

def main() -> None:
    m = M(get_s())
    print(m.read())
    m.write()
    print(m.read())

main()
