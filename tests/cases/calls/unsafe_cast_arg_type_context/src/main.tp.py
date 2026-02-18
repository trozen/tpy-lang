# unsafe_cast should use argument type context from calls and methods.
# Covers builtin method arg, user function arg, and user method arg contexts.
from tpy import Int32, Ptr
from tpy.unsafe import unsafe_cast, unsafe_ptr

carg: list[Int32] = [Int32(1)]

def take_ptr(p: Ptr[None]) -> Int32:
    return Int32(10)

class Sink:
    def put(self, p: Ptr[None]) -> Int32:
        return Int32(20)

parg_list = list[Ptr[None]]()

carg_ptr: Ptr[None] = unsafe_cast(unsafe_ptr(carg))
parg_list.append(carg_ptr)
parg_list.append(unsafe_cast(unsafe_ptr(carg)))  # tpyc: ok

print(take_ptr(unsafe_cast(unsafe_ptr(carg))))  # tpyc: ok
sink = Sink()
print(sink.put(unsafe_cast(unsafe_ptr(carg))))  # tpyc: ok
print(len(parg_list))
