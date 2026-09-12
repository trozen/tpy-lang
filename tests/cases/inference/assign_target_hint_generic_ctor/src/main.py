# Generic constructor type inference from assignment target.
# When T can't be inferred from constructor args, use the LHS type as hint.
from tpy import int32, Own
from tpy.mem import UninitHeapStorage

class Wrapper[T]:
    _storage: UninitHeapStorage[T]

    def __init__(self, val: Own[T]):
        # T inferred from self._storage: UninitHeapStorage[T]
        self._storage = UninitHeapStorage(1)  # tpyc: ok
        self._storage.init0(val)

    def __del__(self):
        self._storage.drop0()

    def get(self) -> T:
        return self._storage.load0()

def main():
    # Annotated var_decl: T inferred from annotation
    s: UninitHeapStorage[int32] = UninitHeapStorage(1)  # tpyc: ok
    s.init0(int32(42))
    print("var_decl:", s.load0())
    s.drop0()

    # Field assignment via generic class
    w = Wrapper[int32](int32(99))
    print("field:", w.get())

    print("done")

main()
