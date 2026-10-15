# Importing a module runs the modules it imports, native ones included, in
# CPython's order and once; each module prints `<section>: <module> init`.
from cls_n import C  # class re-exported by a native module
from fn_n import answer  # function re-exported by a native module
from en_n import Color  # enum re-exported by a native module
from var_n import items  # variable re-exported by a native module
import bare_n  # noqa -- a native module's bare `import bare_c`
from chain_n1 import hop  # native -> native -> regular
import sub_n  # noqa -- a native module's `from pkg_sub import leaf`
import cyc_n  # noqa -- cycle through a native module: cyc_a, then cyc_b
from pkg_npar.types import X  # a native package __init__ runs as the parent
from pkg_nsub.n import D  # a native submodule runs its regular parent first
import pkg_empty.n  # noqa -- an empty native leaf still runs its parent
import nempty_n  # noqa -- native importing an empty native under a printing package
import pkg_own  # noqa -- a package __init__ importing its native submodule
import nn_n1  # noqa -- native <-> native cycle, first consumer
import nn_c2  # noqa -- the cycle's second consumer runs nothing again
import two_u1  # noqa -- first consumer of two_n
import two_u2  # noqa -- second consumer: two_c already ran
import kw_u1  # noqa -- native `new`: its init and `new_`'s stay distinct
import kw_u2  # noqa -- native `new_`
import inline  # module named by a C++ keyword
import local_decl  # noqa -- annotation-only decls inside module-level blocks
from pkg_ns import Thing  # a submodule with its own cpp_namespace, re-exported
import type_  # a regular module whose name ends in `_`
from pkg_kw.delete import erase  # keyword submodule under a cpp_namespace package


def main() -> None:
    c = C()
    c.add("c")
    # The method reads cls_c's global, built by cls_c's init.
    print("class:", c.count())
    print("function:", answer())
    print("enum:", Color.GREEN.value)
    print("variable:", len(items), items[1])
    print("chain:", hop())
    print("keyword_module:", inline.twice(21))
    print("ns_override:", Thing(3).x)
    print("regular_rename:", type_.kind())
    print("keyword_sub:", erase())
    X()
    D()
    print("done")


main()
