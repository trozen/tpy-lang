// Hand-written C++ header for the native enum binding test.
// Values are intentionally non-default (10, 20) to prove TPy reads them
// from the C++ side rather than mirroring auto()-assigned 1, 2.
#pragma once

namespace ns {

enum class E : int {
    A = 10,
    B = 20,
};

}  // namespace ns
