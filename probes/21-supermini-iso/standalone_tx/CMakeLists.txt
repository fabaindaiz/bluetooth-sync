# Throwaway probe for experiment 21 (d-7c8794-3208b7): a SuperMini that emits the probes' BIG on its own,
# powered from a charger, so the other board can measure range, loss and clock drift.
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(standalone_tx)
target_sources(app PRIVATE src/main.c)
