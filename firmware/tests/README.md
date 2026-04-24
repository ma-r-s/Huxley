# firmware/tests/ — host unit tests

Pure-C host-compiled tests for the parts of the firmware that don't
touch hardware. Runs in under a second on a laptop. No board
connection required.

**In scope**: pure functions — protocol parsing, state-transition
tables, value-mapping helpers. Things where the server's expected
behavior is unambiguous and a silent regression would break every
turn.

**Not in scope**: I2S, Wi-Fi, WebSocket client behavior, FreeRTOS
task interaction, or anything that needs a live `hux_app` queue.
For those, see `firmware/tools/smoke.sh` (end-to-end hardware
smoke) and the server-side contract tests under
`packages/core/tests/unit/test_firmware_contract.py`.

## Prerequisites

- `cmake` (≥ 3.16), system `gcc`/`clang`
- ESP-IDF environment sourced so `$IDF_PATH` points at the same cJSON
  the firmware uses:

  ```sh
  . ~/esp/esp-idf/export.sh
  ```

## Run

```sh
cd firmware/tests
cmake -B build
cmake --build build --target check
```

Or directly via ctest:

```sh
ctest --test-dir build --output-on-failure
```

The individual test binary is also runnable:

```sh
./build/test_hux_proto
```

## What's covered

| Target           | Module                                    | Asserts                                                                                                           |
| ---------------- | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `test_hux_proto` | `hux_proto_parse` + `hux_proto_kind_name` | every wire message kind dispatches, malformed JSON is rejected, future fields ignored, field ordering independent |

## Adding a test

1. Write a new `test_<module>.c` alongside the existing ones.
   Include `test_helpers.h`, write `static void test_*(void)` cases,
   wrap a `main` that calls `HX_RUN(test_*)` then `HX_SUMMARY()`.
2. Add the library under test (if it's new) and the test executable
   to `CMakeLists.txt`, then add a matching `add_test(...)`.
3. Keep the module under test free of platform-specific includes
   so the host build stays clean. If it isn't, that's probably a
   sign the module has testable-logic and hardware-coupling mixed
   together — extract the pure part into its own file first.

## Why not Unity or cmocka?

Single-assertion-helper header is ~30 lines, zero build-system
complexity, zero external dependency. If tests ever grow to the
point we need fixtures, parametrisation, or mocks, drop in Unity
and keep going; until then the helper is cheaper than the
framework.
