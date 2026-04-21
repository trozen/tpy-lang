"""Build-side helpers for tpyc: third-party dependency resolution, etc.

This package hosts modules that deal with the boundary between generated
C++ and external build inputs (third-party libraries, bundled sources,
CMake snippet emission). Kept separate from `tpyc/compiler.py` so the
build concerns don't accumulate there.
"""
