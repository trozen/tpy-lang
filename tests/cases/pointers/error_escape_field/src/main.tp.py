from tpy import Int32

class Inner:
    value: Int32
    def __init__(self, value: Int32):
        self.value = value

class Outer:
    inner: Inner
    def __init__(self, inner: Inner):
        self.inner = inner

# Field access on loop-local: _get_expr_scope_depth follows the root
# object, so o.inner has the same depth as o (loop-scoped).
def field_access_escape() -> None:
    saved: Inner = Inner(0)
    for i in range(3):
        o: Outer = Outer(Inner(i))
        saved = o.inner  # tpyc: error(/reference to 'o' may outlive its storage/)
