# tpy.version exposes tpyc's version + is_compiled flag (True when compiled,
# False under the CPython stub). Values come from tpyc.__version__ via
# compile-time macros in lib/tpy/tpy/_version.py. No version-dependent
# state is printed so the output is stable across version bumps and
# identical across the two runtimes.
from tpy.version import __version__, version_info, is_compiled

def main() -> None:
    # String version is non-empty and starts with a digit.
    assert len(__version__) > 0
    assert __version__[0] >= "0" and __version__[0] <= "9"

    # Tuple has the CPython-style shape; components are non-negative
    # and the releaselevel is one of the documented values.
    major, minor, micro, level, serial = version_info
    assert major >= 0
    assert minor >= 0
    assert micro >= 0
    assert (level == "alpha" or level == "beta" or level == "candidate"
            or level == "final" or level == "dev")
    assert serial >= 0

    # Exercise is_compiled as a bool. Exact value differs across runtimes
    # by design; both branches must be type-correct so the mode selector
    # works for downstream dual-target code.
    if is_compiled:
        pass
    else:
        pass

    print("ok")

main()
