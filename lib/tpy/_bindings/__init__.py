# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings")
"""Raw `@native` binding modules. Not for direct user import; consumed by
stdlib facades (e.g. `lib/tpy/re.py` -> `_bindings/pcre2.py`,
`lib/tpy/socket.py` -> `_bindings/posix_socket.py`).
"""
