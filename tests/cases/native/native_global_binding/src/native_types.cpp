#include "native_types.hpp"

extern "C" {
int32_t DG_FrameCount = 42;
int32_t shared_data[] = {10, 20, 30};
int32_t tick = 7;
}

namespace engine {
int32_t score = 99;
}

int32_t lives = 3;
