#pragma once
#include <cstdint>

namespace myns {
inline int32_t namespaced_add(int32_t a, int32_t b) { return a + b; }
}

inline int32_t bare_add(int32_t a, int32_t b) { return a + b; }

#ifdef __cplusplus
extern "C" {
#endif

int32_t c_multiply(int32_t a, int32_t b);

#ifdef __cplusplus
}
#endif
