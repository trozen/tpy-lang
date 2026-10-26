#pragma once
#include <cstdint>

// The C++ defaults differ from every value the test passes, so a raise that
// dropped its argument would show it.
struct NativeErr { std::int32_t code = 9; };

// A throw-tier binding: an exception class whose C++ constructor takes its
// fields in declaration order.
struct NativeThrow : ::tpy::Exception {
    std::int32_t code = 9;
    std::int32_t extra = 9;
    NativeThrow() = default;
    NativeThrow(std::int32_t c, std::int32_t e) : ::tpy::Exception("native"), code(c), extra(e) {}
    TPY_THROWABLE_VIRTUALS(NativeThrow)
};
