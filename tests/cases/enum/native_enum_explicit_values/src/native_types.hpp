// Member values match the TPy-side declaration. The codegen emits per-member
// static_assert pinning the two sides; a mismatch (e.g. C++ flips Alpha to 99)
// would fail at C++ compile time with a clear "does not match" message.
#pragma once

namespace ns {

enum class Tag : int {
    Alpha = 100,
    Beta = 200,
    Gamma = 300,
};

}  // namespace ns
