# Free function with a bytearray param that mutates it: codegen must
# emit a mutable reference. Sema marks the param mutated correctly;
# the gap was that bytearray's param_cpp_formatter encoded const, with
# no separate mutable form for to_cpp_param to use. Now both forms exist
# and codegen picks the mutable one for mutated params.
def pack(dst: bytearray, v: int) -> None:
    dst.append(v)
    dst.append(v + 1)

def borrow(src: bytearray) -> int:
    # Non-mutating param: still uses const ref.
    return len(src)

def main() -> None:
    buf = bytearray()
    pack(buf, 65)
    pack(buf, 67)
    print(len(buf))
    for b in buf:
        print(b)
    print(borrow(buf))

main()
