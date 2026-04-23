#pragma once
#include <cstdint>

extern "C" struct sockaddr_like {
    uint16_t sin_family;
    uint16_t sin_port;
};
