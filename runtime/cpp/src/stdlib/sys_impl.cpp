// sys.maxsize of the build target, bound by the `sys` module through
// `Final[int] = native_global(...)`. Out of line because a BigInt is not a
// constant expression.

#include <tpy/bigint.hpp>

#include <cstddef>
#include <cstdint>

namespace tpy {

// CPython's sys.maxsize is Py_ssize_t's maximum: ptrdiff_t's on the target.
// `extern`: a namespace-scope const would otherwise get internal linkage.
extern const BigInt sys_maxsize = BigInt(static_cast<std::int64_t>(PTRDIFF_MAX));

}  // namespace tpy
