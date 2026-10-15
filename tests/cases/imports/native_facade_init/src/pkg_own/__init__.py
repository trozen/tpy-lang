print("pkg_imports_native: pkg_own begin")
# Imported late on purpose: the two prints bracket the native submodule's init.
import pkg_own.n  # noqa  # tpyc: warning(/Import statement should be at the top/)
print("pkg_imports_native: pkg_own end")
