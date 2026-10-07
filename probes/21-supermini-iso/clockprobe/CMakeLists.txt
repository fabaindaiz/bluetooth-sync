# Throwaway firmware probe for experiment 21 (d-7c8794-3208b7): the SuperMini's 32 kHz clocks.
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(clockprobe)
target_sources(app PRIVATE src/main.c)
