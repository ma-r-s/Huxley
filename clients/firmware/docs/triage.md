# Firmware triage

Firmware-only work tracker. Cross-cutting items that touch the
framework or the wire protocol live in
[`../../docs/triage.md`](../../docs/triage.md) — put them there, not
here.

Items move through the same five gates documented in the main triage
doc:

1. **Validate** — reproduce the problem; capture evidence.
2. **Design** (+ critic if non-trivial).
3. **Implement** — code + regression proof.
4. **Document** — update firmware docs in the same commit.
5. **Ship** — commit reference; entry annotated with commit hash.

Keep entries tight. Link to serial logs, screenshots, or server-log
excerpts via relative paths (store evidence under
`docs/evidence/<id>/` if it's worth checking in).

---

## Active

### F-0014: Talk on GPIO7; act on `set_volume`, `input_mode`, `audio_clear` · bench-tested 2026-10-11 (K2 and a call left for a hand)

**Problem**: the HX-1 case's talk lever closes a switch on header pin 2
(GPIO7) to GND, but the firmware only read the board's K2. And against
today's server three messages were logged and ignored: `set_volume`
("sube el volumen" did nothing), `input_mode` (a Telegram call heard
silence from the device: the mic only ran while talk was held) and
`audio_clear` (an audiobook seek or an interrupt played the old tail
first). Also: a failed I2C read of K2 counted as a press (the expander
read returned "low" on error, and the button is active-low).
**Solution** (branch `firmware-talk-gpio7`): talk = GPIO7 (pull-up) OR
K2, through a host-tested debounce (`hux_debounce`); `hux_board_tca_read_level`
reports errors, an unreadable K2 is not pressed. `hux_proto` parses
`set_volume.level`, `input_mode.mode`, `state.value`,
`hello.session_ready`, and `audio_clear`. `audio_clear` is handled on
the data plane in order with `audio`; the speaker ring is reset by its
reader (`spk_task`) on request. The mic runs while talk is held or
while `input_mode` is `skill_continuous`. A release whose press was
ignored no longer sends a stray `ptt_stop`; a WS drop stops the mic.
**Evidence so far**: `idf.py build` clean (no warnings, 1.0 MB, 52 % of
the app partition free); host tests green (hux_proto 92 assertions incl.
the new payloads, hux_ws_frag, hux_debounce); server contract tests
green both ways (new: server frames vs what the firmware parses,
`"type"` within the probe's 100 bytes). Not yet run on the board.
Review round (fresh critic, same night): no blockers ("would flash as
is"). Fixed: the volume scale (esp_codec_dev's default curve put 50 at
-25 dB while the server's system skill assumes 50 to start; now 50 =
-15 dB, the old boot loudness, boot at 50, floor 10); clears read the
ring out instead of resetting it (a reset can race the writer's
lock-free send and fill the ring with 0x55, a loud hum); the type probe
decides on the first "type" key only (a nested one in a dev_event could
clear the speaker), now in `hux_type_probe` with host tests; the
contract test's state check; docs and comments.
**Open product question for Mario**: during a skill's claim (a Telegram
call) the device's mic now streams continuously, as the PWA does and as
the protocol's mic mode says. It is the first time the device has an
open mic without the lever held. Calls need it to be two-way; if the
walkie-talkie rule should hold even in calls, it is one line in
`hux_app.c` (`mic_update`).
**Bench test, 2026-10-11** (the original Huxley board, speaker amp
off, no sound in the room; see F-0015 to F-0019 for what it found):
GPIO7 pressed electrically through JTAG (pull-up flipped to pull-down in
IO_MUX: `button.talk press src=lever`, Mic1 streamed 22 frames, release
ended the turn); answers play (reply audio reaches the codec, 0 write
errors, verified electrically by the loopback self-test); "pon el volumen
al 80" by injected speech -> `set_volume 80` -> ES8311 register 0xB5;
`audio_clear` by barge-in; "pon la radio" streamed 27 s at real time
with no underrun, "para la radio" cleared it 81 ms after the press. **Left for a hand**: K2 (behind the I2C
expander, JTAG can't press it; its reads never failed in hours of
polling) and a real Telegram call (`input_mode`).
**Known gaps** (not in scope): no echo cancellation during calls; the
speaker leaks into the mic.

### F-0015: Long replies stalled, dropped and played choppy · fixed 2026-10-11, measured on the board

**Problem**: a long answer ("cuéntame un cuento largo") died 15 s in:
the socket heard nothing until TCP keepalive killed it, and the rest of
the reply was lost. With that fixed, the reply arrived at 0.65x real time
and the speaker ran dry hundreds of times (choppy); with that fixed, it
arrived 5-6x faster than real time and the 512 KB ring dropped 60 s of
an 80 s story.
**Evidence** (all on the board, `say 3`, amp off):
1. JTAG snapshot mid-stall: the WebSocket's TCP control block had
   `ooseq` = 32 segments (46 KB), the in-order queue empty (`rcv_wnd`
   65535). lwIP kept out-of-order segments without a cap
   (`TCP_OOSEQ_MAX_PBUFS=0`, IDF's default with PSRAM) against a 64 KB
   window and 32 Wi-Fi RX buffers: after one lost frame the out-of-order
   queue held every RX buffer and the retransmission had nowhere to land.
   IDF's Kconfig describes exactly this.
2. `ws.rx.stats` (new): 0.65x real time, gaps of ~250 ms (lwIP's TCP
   timer). Wi-Fi driver counters clean (`wifistat`: no RX buffer
   failures), ping to the board 0 % loss at 140 KB/s, the server's socket
   pinned at 128 KB queued. Cause: `LWIP_TCP_RECVMBOX_SIZE=6`; lwIP's rule
   is window / MSS + 2 (25). The mailbox filled while the WS task handled
   a chunk, lwIP refused the next segments and retried on its 250 ms timer.
3. With 1 and 2 fixed: 363 KB/s, an 80 s story in 14 s, and 364
   `spk.ring.overflow` (59.5 s dropped): the ring dropped when full.
4. Backpressure alone (wait for room) kept the audio but let macOS grow
   the server's socket buffer (`net.inet.tcp.autosndbufmax` 4 MB): the
   server's keepalive ping queued behind 30 s of audio, the server failed
   the connection after 20 s, and F-0016 left the device dead.
**Fix**: `TCP_WND` 32 KB, `TCP_OOSEQ_MAX_PBUFS` 8, Wi-Fi dynamic RX
buffers 40, `TCP_RECVMBOX_SIZE` 32 (sdkconfig.defaults, with the story);
the speaker ring is 4 MB in PSRAM (87 s, a whole reply; 3.4 MB PSRAM
stays free) and `hux_audio_spk_push` waits for room instead of dropping
(re-checking the barge-in hold every 20 ms; gives up after 2 s if
playback is stuck). A clear marks the ring stale and reads it out over the
next passes (a full 4 MB ring empties in ~0.6 s; the caller isn't held).
**Verified**: 46 s and ~150 s stories: 0 underruns, 0 drops, received =
played to 0.4 %, server send queue <= 132 KB, server -> board lag of the
last message 2.8 s (was 29.8 s), no disconnect. `spk.underrun` (new) and
`ws.rx.stats` (new) watch it from now on. **Residual**: a reply longer
than 87 s falls back to backpressure and the server-side buffer growth
(main `docs/triage.md` T1.32).

### F-0016: Any close from the server left the device dead until power-cycled · fixed 2026-10-11

**Problem**: esp_websocket_client's task exits on a CLOSE frame unless
`enable_close_reconnect` is set (it defaults off) and emits `CLOSED` /
`FINISH`, which `hux_net` didn't handle. The app stayed READY on a dead
client: no reconnect, presses sent nothing, no offline cue. Every server
close does this: an eviction by another client, a server shutdown, the
keepalive timeout of F-0015.
**Evidence**: after the story, JTAG showed no `websocket_task` and no
active TCP control block while the app logged READY; the server had
logged `client_disconnected close_code=1006` at the same second.
**Fix**: `enable_close_reconnect`; `CLOSED` posts a disconnect to the app;
`FINISH` (the task ended) makes the app restart the client. An eviction
(close 1001 "Replaced by new client", parsed from the CLOSE frame) pauses
the client until the next talk press, as an evicted PWA tab waits until
it is shown: reconnecting at once would evict that client, and the two
would trade places forever.
**Verified**: a test client took the server: `ws.rx.close code=1001
eviction=1`, paused; a talk press played the offline cue and the board
was READY 62 ms later.
**Review round** (fresh critic, same night; all fixed, bench 9/9 after):
a FINISH reaching the app after a press had restarted the client could
loop stop/start forever (the client's liveness is now one flag, set by a
start and cleared by the task's own FINISH; `resume` never stops a live
task); a Wi-Fi reconnect restarted an evicted (paused) client (only the
first IP starts it now; the app owns every later start/stop); a redial
queued before the eviction could leave the app READY on the stopped
client (the pause posts a disconnect after it); a client that couldn't
restart is restarted by the next press.

### F-0017: Barge-in played the old reply over the user for 1.5-2.5 s · fixed 2026-10-11

**Problem**: a press clears the ring, but the reply audio the server had
already sent kept arriving and played until the server's `audio_clear`
(it follows that audio in the stream): measured 1.55 s, then 2.5 s.
**Fix**: on a press while the server says it is speaking
(`model_speaking`, now parsed), the speaker holds: inbound audio is
dropped until `audio_clear` (or `model_speaking: false`, or 3 s). The hold
goes on before the clear so a write waiting for room can't land in the
emptied ring. Host test for the `model_speaking` parse; contract test
for its shape.
**Verified**: press 25 s into a story with the 4 MB ring full: 4.2 MB
marked stale, 167 KB in flight dropped, `audio_clear` 612 ms after the
press, the new answer played.
**Review round**: a chunk the WS task was still writing into a full ring
when the press cleared it landed after the clear and played; spk_task now
plays nothing while the hold is on, and `audio_clear` clears before it
ends the hold. The hold's expiry could wipe a newer press's hold: one
atomic word (press time | 1), expired with compare-and-swap.

### F-0018: Smaller defects found on the bench · fixed 2026-10-11

- **Volume clipped at the top**: esp_codec_dev's default "PA headroom"
  (a 5 V PA, a 3.3 V DAC) added 3.6 dB to every level: 100 was +3.5 dB
  of digital gain, so a full-scale reply clipped in the DAC. Same rail =
  0 dB; the curve's middle moved to -11.5 dB so 50 keeps the loudness of
  every test so far. Read back from the ES8311 register (and by loopback):
  10 / 50 / 80 / 100 = -42.5 / -11.5 / -5.0 / 0 dB.
- **Wi-Fi gave up after 5 retries** (`wifi.retries_exhausted`, then never
  again): a router reboot left the walkie offline until power-cycled. Now
  5 quick tries, then 1, 2, 4 ... 30 s, forever (`hux_wifi_retry`, host
  test); the disconnect reason is logged. On the board with an SSID that
  doesn't exist: reason 201, retries at 0 x5, 1, 2, 4, 8, 16, 30, 30 s.
- **Joined the first AP heard, not the strongest**: on the mesh the board
  sat at -26, -60 and -87 dBm on three boots a metre from a node. Full
  scan, strongest first (~3 s more at boot); `wifi.associated` logs the
  BSSID, channel and RSSI.
- **The WebSocket started before Wi-Fi had an IP**: a false "Host is
  unreachable" error every boot and 2 s lost. It starts on the first IP
  (READY at 2.2 s, was 4.1 s, before the full scan).
- **I2S underflow replayed the last buffer** (a stutter or buzz if
  spk_task is ever late): `auto_clear` sends zeros instead.
- **A press while offline couldn't stop playback** (the ring can hold a
  whole reply from before the drop), and its cue waited for the ring to
  drain: it clears the ring, then plays the cue (critic). Known edge: if
  the server was still sending the reply, it sends the rest after the
  reconnect (it never heard that press).
- **Stack headroom**: `hux_button` had 532 B left of 3 KB (now 4 KB),
  the WS task 1.2 KB of 4 KB before its first reply (now 6 KB), the log
  drain 688 B of 4 KB after a 12-turn soak (now 6 KB). `info` reports
  every task's minimum; `bench.py` fails below 512 B.
- `reset=UNKNOWN` for USB/JTAG resets; `ring_used` read 1 when empty.
- The original board's coredump partition held stale data (a false
  "Incorrect size of core dump image" error every boot): erased.

### F-0019: Bench tools: test the board with no hand and no sound · done 2026-10-11

The serial console (`hux_console`): `talk` / `release` (a third talk
input, debounced with the lever and K2), `say <n>` (a whole turn: a clip
from flash replaces Mic1 while talk is held; `tools/clips.py` renders
Spanish questions with macOS `say -o`, nothing is played), `selftest`
(electrical loopback: a tone on the DAC read back on the ES7210's echo
reference channel; pass = 80+ dB rise, mics alive), `tone`, `vol`,
`pa off|on` (silent bench), `wifi off|on`, `wifistat`, `info` (heap,
PSRAM, every task's stack minimum, ring, volume and the ES8311 register,
RSSI, time since the last inbound data). Also new on the wire: `client_event
device` on every connect (firmware version, reset reason, boot counter,
RSSI, volume: the server log now says which firmware is talking) and
`mic peak_dbfs` in each press's stats. `tools/bench.py` runs the whole
bench check list and prints PASS/FAIL. Review: a `say` whose press was
ignored left its clip armed for the next real press; the console disarms
it when the say ends.

### F-0020: The walkie got stuck in download mode inside its closed case · fixed 2026-10-12

**Problem**: plugged into the Mac after running on battery, the walkie
went silent: `rst:0x15 (USB_UART_CHIP_RESET),boot:0x0
(DOWNLOAD(USB/UART0))`, `waiting for download`. Every reset after that
(esptool's RTS reset, my logger's, a JTAG reset) came back the same;
only a power cut (rocker off, USB out) brought it back, and Mario had
to open the case.
**Cause** (esptool docs, "Advanced Options"; Espressif staff on
esp32.com): on the S3's USB-Serial/JTAG the host's RTS line is a core
reset that doesn't re-read the boot pins, so a chip a host once reset
with the boot pin overridden low stays in download mode. Opening the
port with DTR/RTS toggling (my logger, `idf.py monitor`, terminals) can
do that, and `idf.py flash` ends with an RTS reset. GPIO0 read high
throughout (esptool `read_mem` of GPIO_IN): the case doesn't press BOOT.
The S3 can't refuse a host's reset in software (C6 and later can;
`RTC_CNTL_USB_RESET_DISABLE` doesn't, per a forum report).
**Root cause** (found after the first fix, from the schematic): the
TCA9555 expander's INT# output is wired to GPIO0 through R15 (0 ohm).
INT# goes low on any input change and stays low until the changed port
is read; the firmware only ever read port 1 (K2), so after any change on
port 0 (EX1-EX3 on the header, RTC and card-detect lines) GPIO0 stayed
low and every reset short of a power cut booted into download mode. A
power cut resets the expander too, which is why only that helped; on
the bench nothing on port 0 moved. Fix (88baa3aa): both ports are read
every 10 ms and at boot; port-0 changes are logged (`tca.port0 change`).
Verified: GPIO0 sampled high over JTAG before the flash; `flash.sh`'s
watchdog reset booted the app by itself, case closed. Not all of it:
the next flash (1cd6e68c) landed in download mode again: GPIO0 read high
in the ROM but had been latched low at the reset, so something still
pulls it low at the instant of a reset right after flashing (unknown;
the same reset a minute later booted). `flash.sh` now checks whether
the ROM still answers and resets again, up to 8 times; the flash after
that came back by itself in 13 s.
**Also**: `tools/flash.sh` flashes and ends with esptool's watchdog reset
(a full reset that re-reads the pins) and `--unstick` boots a stuck
board from the Mac; `tools/serial_log.py` (and `serial-capture.sh`,
`bench.py`, `smoke.sh` on top of it) hold DTR and RTS still; the console
has `reboot` (esp_restart) so tools never reset the board over USB. The
README, debugging guide and CLAUDE.md say not to use `idf.py flash` /
`monitor` on this board. On battery or a plain charger nothing drives
those lines: a walkie in use can't get there.

**Found 2026-10-12 by the case session**: the HX-1 tub top's corner block touched the BOOT button's plunger with 0.00 mm clearance (the CAD followed the board's outline, buttons included); a top printed a hair proud could have held BOOT in, which at any reset boots download mode. Fixed in the enclosure (a 0.6 mm pocket for every edge button). The hardware fix for the INT# path is R15's removal (F-0026).

### F-0021: Updates and debugging over Wi-Fi · done 2026-10-12

**Problem**: every firmware change meant the USB cable, and the walkie
lives in a closed case (4 screws, ~20 cables). Mario: "so I don't ever
have to connect this device again to this computer unless absolutely
necessary", and "look what people have built ... and implement that".
**What others do** (looked up, not invented): ESP-IDF's own
`esp_https_ota` with app rollback (the `advanced_https_ota` example,
the base of ESPHome's and Tasmota's updaters); Tasmota/ESPHome's
pattern of a small authenticated HTTP endpoint on the device; ESP-IDF's
core dump to flash, fetched later. The Fleet project's push-style OTA
marked images valid only after a network check, which rolls back a
good image when the server is down; this one doesn't.
**Built**: `hux_ota` (on-demand `esp_https_ota` into the other slot;
refuses another project's image and the running version unless forced;
a new image confirms itself after 30 s up, else the bootloader boots
the old one; revised after review, see below), `hux_dev` (port 80, token in `X-Huxley-Token`: `GET
/info`, `POST /cmd` = any console command, `GET /coredump`), console
`reboot` and `ota <url> [force]`, `tools/dev.py` (finds the board in the
server log, serves `build/`, follows the board's lines, keeps each
flashed ELF as `build/elf/<version>-<sha9>.elf` and decodes a dump with
the one whose hash it carries).
**Evidence** (on the board in its closed case, no cable used for any of
it): `dev.py flash` 02cc5438 in 11 s, confirmed 30 s later in ota_1; a
test image that aborts in `app_main` was installed, crashed, and the
bootloader went back to 02cc5438 by itself; `dev.py coredump` fetched
the 64 KB dump over Wi-Fi and decoded `abort()` at `app_main
main.c:118` with the test image's ELF (picked by hash, not the running
one); flashing the running version prints `not updated: ... ota.refused`
and exits 1.
**Gaps**: after that rollback the old image reported `reset=SW_RESET`,
not `PANIC`, so the reset reason can't tell a rollback apart; `/info`
now has `last_invalid` (the slot and version the bootloader rejected)
and `dev.py flash` prints it when an update doesn't stick.
HTTP, not HTTPS: fine on the home LAN, the token is the lock. The
bootloader and partition table still need USB.
**Review** (fresh critic, 2026-10-12), acted on: the 30 s timer kept
images that couldn't take the next update (dead door, bad Wi-Fi
settings, a hang): now `dev.py flash` confirms over the door after 30 s
(`ota confirm`), so a kept image is one that was reached and updated
over Wi-Fi; `dev.py flash` printed "done" while still pending and
matched versions by string (an uncommitted `X-dirty` build matched the
old image reconnecting): it now compares the ELF hash in `/info`
(`elf_sha`), checks the slot changed and waits for `valid`; a remote
`wifi off` locked everyone out (now `wifi off [s]` comes back); `ota`
rebooted the walkie mid-reply (refused while playing or listening unless
`force`); the image server shared all of `build/` (the ELF holds the
Wi-Fi password and the token): only the image now; the door accepted
`change-me` and could take every socket: weak tokens refused, a build
without one is an error, 3 sockets; `ota rollback` added. Not acted on
yet: an automatic go-back after N crashes in a row past the confirm
(medium; filed as F-0023), core dumps never erased after reading
(low).

### F-0022: Measure the link instead of guessing; stalls drop the connection · in_progress 2026-10-12

**Problem**: Mario, playing music on the walkie (case closed, on
battery): "It's being choppy all of a sudden." I answered from clues
(RSSI went from -27 to -48), and Mario: "I don't want you to guess
anything. We have complete control of the firmware and the software on
the servers. Why don't you create a reliable way to test stuff".
**Evidence** (server log, 2026-10-12): at 08:03:26, 08:03:35 and
08:08:48 the board opened a new WebSocket in the middle of a talk press
(the server evicted the old one, close 1006); the press at 08:08:46
reached the server 2.3 s late first. From 08:04 the music arrived slower
than real time (`ws.rx.stats` 300 ms of audio in a 1529 ms window,
`events` well above `msgs`) and the speaker played 57 gaps of 20-940 ms.
The board's own side of those drops never reached the server: lines
logged while the WS is down were dropped.
**Cause of the drops** (read, not guessed): esp_websocket_client tears
the connection down when a send can't finish in its timeout
(`esp_websocket_client.c:705`, `wlen == 0` -> `abort_connection`), and
every send uses `WS_SEND_TIMEOUT_MS` = 200 ms. The mic sends ~66 KB/s; a
link that slows below that fills the TCP buffers in a few seconds, and
the next 200 ms wait kills the connection and the question with it.
**Built (the harness)**:
- Server `server.tx.audio_stats` once a second while audio flows: audio
  handed over, time spent waiting in `ws.send`, the asyncio write
  buffer, and the kernel's unsent bytes (`SO_NWRITE` on macOS,
  `TIOCOUTQ` on Linux). Next to the board's `ws.rx.stats` (now with
  `rssi`) it says whether the network or the source was slow.
- Board log lines survive a disconnect: the sink says whether a line
  went out, the drain task holds it and retries, 256-line queue in
  PSRAM; boot lines (update check, Wi-Fi AP, previous crash) are queued
  too and arrive on the first connect.
- Console `server <uri>` (until reboot) and `wifi off [s]`, which comes
  back by itself (from the review: over Wi-Fi a plain `wifi off` locked
  everyone out until a power cut).
- `tools/bench.py --wifi`: the check list over Wi-Fi (developer door +
  server log), and two fault steps through Toxiproxy (Shopify's tool
  for this): `stall` (uplink at 1 KB/s for 5 s mid-question; pass = no
  reconnect, the question is heard) and `slowdown` (downlink capped
  mid-stream; prints server-sent next to board-received per second).
**Fix** (built; verified below once the board is free): sends wait up
to 5 s for room in the socket (`WS_SEND_TIMEOUT_MS`, was 200 ms): the
library only aborts a link with no progress for 5 s, a slow one keeps
going. The mic queue is 10 s in PSRAM (was 1 s). `ptt_start`, the frames
and `ptt_stop` go through that one queue in order: `ptt_stop` used to
leave from the app task and overtake frames still queued, so a backlog
cut the end off the question. `mic.tx.done` logs when `ptt_stop` really
goes out, with the press's totals.
**Results** (2026-10-12 night, case closed, amp off, over Wi-Fi):
- `stall` (uplink stopped 3 s, 1 s into a 5 s question). Before the fix
  (856c5385): FAIL, `ws.send.incomplete requested=1306 sent=0`, a
  reconnect, the question never heard (the field failure, reproduced).
  After (83e58a54): PASS, 0 reconnects, backlog peaked at 86 frames and
  drained, 287/287 frames sent, `ptt_stop` after the last, the question
  heard word for word.
- First try of `stall` through Toxiproxy passed on the broken build: on
  macOS (no root) its sockets autotune to megabytes, the Mac swallowed
  ~250 KB and the board never felt the stall. `bench.py` now has its own
  proxy (LinkProxy) with fixed 8 KB socket buffers.
- `slowdown` (downlink at 32 KB/s, ~40% of real time, for 8 s, radio):
  the server sent on time (1100 ms per ~1050 ms window), the board got
  400-500 ms per second: the network, measured. 25 dropouts (3.4 s of
  silence). With rebuffering (after a dropout mid-reply, wait for 1 s of
  audio before playing on): 2 pauses (2.2 s, 1.5 s; 3.7 s total).
- Full `bench.py --wifi --reset`: boot, selftest, turn, volume, bargein,
  health pass; offline and evict failed on the bench's side (a `wifi
  off` cut its own HTTP reply; eviction lines arrive only after the
  reconnect): both fixed in the bench/console.
- Also found: a silent press transcribes as "Subtitulos realizados por la
  comunidad de Amara.org" (Whisper on silence) and gets answered (server,
  fixed separately); board log levels arrive as `info` (the server's
  structlog overwrites the client's `level`; server fix); ~30 empty
  `wifi:` lines per boot (the Wi-Fi blob logs header and text apart):
  no longer streamed.
- Re-run of the full list on 5c88849c: everything passed except
  `offline`, a real one: Wi-Fi back at board time 45.3 s, the server
  again only at 63.2 s. The drop was at 33.17 s and the new connection
  came 30.05 s later: the WebSocket's reconnect attempt started just
  before the interface went down, and its TCP connect waited out the
  library's `network_timeout_ms` (30 s) on a SYN into a dead link. In
  the runs that recovered in under 1 s the attempts started after the
  interface was down and failed at once (every 3 s). The race can't be
  forced from outside; `network_timeout_ms` is now 5 s (the connect,
  ping/pong writes and the rest of a half-read frame), bounding it. The
  offline step now measures IP-to-READY (pass under 10 s), idle and in
  the middle of a slowed reply.
- Review (fresh critic, 2026-10-12 night), acted on, 979fbc7b: a
  stalled mic send holds the library's tx lock up to 5 s and the
  WebSocket task's PONG waited only 2 s for it, then aborted: a server
  ping during a stall still dropped the question (the first `stall` PASS
  was luck against a 20 s ping cycle). bench.py's proxy now injects a
  ping at a frame boundary mid-stall: d5b0bc8c FAIL, 4 reconnects;
  979fbc7b (tx lock timeout 6 s, build-checked > send timeout) PASS, 0
  reconnects, 984/984 frames. Also: the last 20 ms of every question
  was lost (mic stop didn't wait for the frame in flight; now 288/288
  frames where it was 287), `server <uri>` reverts by itself after 15
  min and can't race the app's resume, `quiet` capped at 12 h,
  rebuffering waits for 60 ms dry, bench restores in a finally and
  leaves a quiet period alone, `stall` checks the whole question went
  out, dev.py confirms an image left pending by an interrupted flash.
- New bench tools tonight: `silent` (an empty press must not be
  committed; fails on the server until its gate lands), `dev.py clips`
  (bench clips over Wi-Fi, POST /clips) with the requests that failed in
  real use as clips 7-15.

### F-0023: Go back by itself after repeated crashes past the confirm · queued (required by F-0028, ignoring BROWNOUT)

**Problem** (from the F-0021 review): an image that passes its 30 s and
is confirmed, then crashes on every talk press, crash-loops for good;
`ota rollback` needs someone to reach the door between crashes.
**Solution**: count panics with short uptime in RTC memory (the boot
counter is already there); after 3 in a row, `esp_ota_set_boot_partition`
to the other slot if it's valid, and say so (`ota.auto_rollback`).
**Effort**: half a day with a crash-on-press test image.

### F-0024: Opus audio over the WebSocket (the walkie's link drops packets) · in_progress 2026-10-12

**Problem**: playback cut for seconds ("plays half a second, cuts for 2
or more"; 34 % of playing time was gaps at 08:00-08:30). Measured on both
ends: the server sent on time; the walkie's 2.4 GHz link lost packets in
episodes (ping loss up to 8.3 %, p90 up to 665 ms, against 0 % to the
router at the same time); uncompressed PCM as base64 JSON is ~64 KB/s,
and TCP over that loss caps far below it. Not the amp (on/off A/B: gaps
0/57/49 s with it on, 15/0/24 s off), not the channel width (20 vs 40
MHz A/B, no difference).
**What others do** (Mario: "don't reinvent the wheel"): xiaozhi-esp32,
the most used open-source ESP32 voice assistant (Espressif ships it as a
component), sends Opus, 60 ms frames, one raw packet per binary
WebSocket message, negotiated by `audio_params` in the hello, JSON for
everything else on the same socket; Espressif's esp_audio_codec on the
device, `opuslib_next` on its Python server.
**Built (firmware)**: `?audio=opus` on the WebSocket URL (the opt-in); the
server's hello `audio_params` {opus, 24000, 1, 60} turns it on, anything
else stays base64 PCM; `hux_opus` (esp_audio_codec ~2.5.0): mic encoder
VOIP 24 kbit/s complexity 3 (packets <= 280 B), 20 ms mic frames gathered
into 60 ms packets, a short last one padded before `ptt_stop`; received
binary packets queued (PSRAM, 2.9 s) to a decoder task that feeds the
same speaker ring, with `audio_clear` through the same queue so it lands
after exactly the packets before it; the queue is dropped on a
disconnect. Stats: `mic.tx.stats opus_bytes enc_us`, `ws.rx.stats opus
dec_us`. Verified so far: init ok, internal heap 137-146 KB free, PCM
fallback with today's server (turn, silent, health PASS).
**Next**: the server side (in progress), then the bench on Opus (turn,
stall, slowdown, resume, hearing) and `dev.py link` over real use.

### F-0025: Say how much battery is left · done 2026-10-12 (1b0fe784, probe fix below) (the wiring in F-0026's open-case session; Mario approved the case opening once)

**Ask** (Mario): "a way so the device can tell me how much battery is
left." Nothing reads the battery today.
**Validate**: the board's schematic (ESP32-S3-AUDIO-Board v1.1) divides
the cell by 3 (R2 200K over R11 100K, 200 nF) onto BAT_ADC, but its note
says battery measurement "is disabled by default": BAT_ADC reaches GPIO1
only through jumpers R53 + R78, or GPIO6 through R78, and stock boards
ship them open (the camera loses CAM_HREF on GPIO1; we have no camera).
**Probe** (`hux_battery`, console `battery`): reads GPIO1 and GPIO6
floating, then pulled down. A wired divider reads cell/3 (~1.3 V) and
holds ~0.5 V against the pull-down; an open pad reads noise, then ~0.
One `battery.probe gpio=N ... verdict=wired|not_wired` line per pin.
**Design** (if wired): the board pushes `client_event system.battery`
{mv, pct} on connect and every few minutes; the system skill subscribes
(`ctx.subscribe_client_event`), keeps the last reading, answers a
`get_battery` tool from it (saying when it is old or missing), and
speaks a warning when a reading crosses 20 % and 10 %. No new framework
concept: client_event already carries device telemetry to skills.
**Probe result** (c9a022d2 on the walkie, 2026-10-12): GPIO1 floats at
2632 mV (spread 725) and drops to 0 pulled down; GPIO6 2594 mV (spread
351), pulled 3 mV. Both `not_wired`: a wired divider would read ~1.3 V
and hold ~0.5 V. Mario: the original firmware showed a level with
nothing soldered. Waveshare's Arduino demo for this board reads GPIO8
(x3); its factory image and xiaozhi's board code read nothing. Probe
widened to every free ADC1 pin (1f1d57c3): GPIO 1-6, 8, 9 all float
(2373-3030 mV, spread 192-748) and fall to 0-38 mV pulled down. No pin
carries the cell on this board. The level Mario saw two days ago came from
this board's previous firmware (Fleet's statue): it read GPIO8 with a
x1.5 "divider" fitted so the reading looked like a full cell (x3 gave
8.3 V), and a guard that hid its "intermittent garbage". That is a
floating pin's 2.6-3.0 V, which our pull-down test drives to 10 mV. No software path gives a level (the charger's STAT only
drives the LED; the 3.3 V rail is regulated until the cell is empty).
**Blocked on Mario**: open the case and either bridge R78's pads 1 and 3
(BAT_ADC to GPIO6, the board's own 1 % divider), or wire 200K from the
cell's + to header IO6 and 100K from IO6 to GND. Either lands on GPIO6;
`battery` then says `gpio=6 ... verdict=wired`, and the design above
gets built.
**What the board has** (Mario looked, 2026-10-12): both are 3-pad groups with a 0R fitted. Upper group: 0R across middle and right; lower: 0R across middle and left (board held BAT/SPK plugs on the right). The schematic's "0R NC" puts each factory 0R across pins 2-1 (R53: CAM_HREF-GPIO1; R78: BAT_ADC to the link toward R53's pin 3, a dead end); that is the only fitting consistent with the probe (GPIO1 and GPIO6 both unconnected), so on R78 pin 2 is the left pad and the empty right pad is pin 3 (GPIO6). **The fix**: keep both 0R parts, add one solder blob on the lower group joining the middle pad to the empty right pad (BAT_ADC to GPIO6). Held the other way round, the same instruction lands on R53 instead and puts BAT_ADC on GPIO1 (through R78's 0R and the link): also readable, the probe covers both pins. Picture sent to Mario (`battery_bridge_exact.png`).
**Built ahead of the blob** (2026-10-12): firmware `hux_battery_start` (finds GPIO6 or GPIO1, median of 32 at 6 dB once a minute while quiet, `hux_battery_level.c` host-tested: a Li-ion curve, a fall followed at once, a rise only past 5 points), `system.battery {mv, pct}` after each hello, on a change and every 5 min, `battery cal <mv>`; the system skill keeps the last reading for `get_battery` (says how old past 15 min, "not reported yet" before any) and speaks once at 20 % and once at 10 % (re-armed above 30 %), the warning as its own task (the handler runs in the receive loop). Waiting on Mario's blob to see `battery.wired gpio=6`, then a multimeter reading for `battery cal`.
**Done on the walkie** (2026-10-12 20:20Z): Mario's blob landed on the upper group (R53: GPIO1 to the link, through R78's 0R to BAT_ADC), so the cell reads on GPIO1, as predicted for that case; GPIO6 and every other pin still read empty (no stray bridge). First boot: `battery.wired gpio=1`, 3.66 V uncalibrated = 18 %, and the system skill spoke the 20 % warning. A multimeter read the cell at 3.79 V: `battery cal 3790` set `cal_ppm=1026265` (the ADC read 2.6 % low), and the level became 3.81 V = 53 %. **Lesson**: around 3.6-3.8 V the curve is steep (130 mV is 30 points), so an uncalibrated board can warn falsely; every board gets one multimeter reading. Found doing it: the console probe read the level pin with the 12 dB scale after the level task had set it to 6 dB (1.9x too high, `verdict=not_wired`); fixed, the probe uses that pin's own scale.
**Where it stopped (before)**: R78 and R53 are 0603 pads on the board's bottom
(the BAT / SPK side), ~2 mm apart (Waveshare's STEP: R78 x 19.4-21.0,
y 31.7-32.5; R53 y 33.6-34.4, board coordinates). Next: map them onto
Waveshare's bottom photo for Mario, or take option B (no map needed).

### F-0026: The open-case session before handover (remote-operation W7) · queued 2026-10-12

**Why**: the walkie goes to grandpa's retirement home and the case opens once more, never again (Mario, 2026-10-12; plan `docs/remote-operation.md` W7, server side T1.43 owned by the "Huxley improvement plan" session). Everything that needs the open case or the USB cable happens in that one session.
**Hardware** (a guide for Mario, with photos of where to solder, before the day): battery to IO6 (bridge R78 pads 1-3, or a 200K/100K divider; F-0025's probe confirms `gpio=6 ... wired`); VBUS through a divider to a free ADC1 pin (charging detection: the charger's STAT only drives the LED); remove R15 (the 0 ohm link from the expander's INT# to GPIO0: no reset can ever boot download mode again, F-0020); check the cell has a protection board. (A pinhole to RST was planned; Mario dropped it, 2026-10-12: no holes, no extra prints. Recovery is the firmware's job, F-0027.)
**Flash** (the last USB flash): partition table with OTA slots 2 to 4 MB each (image 1.3 MB today), NVS 24 to 64 KB, coredump 64 to 256 KB (staged as `partitions-w7.csv` until the day: the device's table only changes over USB); final bootloader; full flash dump and `espefuse summary` saved.
**Battery firmware** (F-0025 reporting side): two-point calibration against a multimeter, median of idle samples, Li-ion table with hysteresis, 6 dB attenuation; `client_event system.battery` and the skill tool; low battery: a clip once, then deep sleep with a timer wake.
**Tests**: drain to cutoff with the rocker on, plug in, boots and reconnects alone; a realistic day on battery; current with Wi-Fi power save off vs on.
**Prep done 2026-10-12**: the handover table is staged (`partitions-w7.csv`, 29704bfc, validated with gen_esp32part.py; on the day also move clips.py / flash.sh to storage 0x820000). Parts located from Waveshare's STEP (assembly placements composed onto local leaf boxes), registered onto their underside product photo (fitted on the BAT and SPK plugs, checked on three chips within 2 px): **R78 and R53 are the two stacked 3-pad boxes near the top centre of the underside** (the BAT/SPK plugs on the right); **R15** (3 pads: pad 1 INT#, pad 2 3V3, pad 3 GPIO0; the fitted 0R joins 1-3, which is F-0020's download-mode trap) is on the underside about 12 mm left of centre, within about 1 mm: confirm on the day with a continuity beep from the BOOT button's GPIO0 side. **Charging detection needs no board soldering**: header P1 pin 1 is USB_5V (silkscreen "5V"), so 100K from 5V to a free ADC1 pin (IO8) and 100K from that pin to GND reads about 2.5 V while charging and 0 V on battery. The cells are protected 18650s (the enclosure decision): check each has its protection board when the case is open. **No RST pinhole** (Mario, 2026-10-12: "I don't want to make any holes or print anything else"; the case session had designed one and took it back out; the 0.6 mm clearance round the edge buttons stays, which fixes BOOT's 0.00 mm contact). Staff can't reach the board: a hung walkie recovers only through the firmware (F-0027's watchdog supervisor, escalation ladder and remote reboot; the black box for why). Whether staff can power-cycle it depends on the rocker (the case CAD says fingertip-reachable in the left wall, an older note says not; Mario is checking, 2026-10-12); either way the watchdog has to cover every hang. Idea from the case session for the battery firmware: alert when 5V is present (IO8) but the cell isn't rising, i.e. the rocker left OFF while plugged in (the charger is on the board's side of the rocker). **Parts decided**: battery level by a solder blob on R78's middle and empty pad, keeping the factory 0R (no parts; see F-0025 for what the board has); charging detection by two 100K through-hole resistors built inline in the harness (header 5V -> 100K -> junction -> 100K -> GND, junction wire to IO8), heat-shrunk in the cable channel like the lever's wires; battery fallback (only if the bridge fails) 200K (2 x 100K) from the cell's + to IO3 and 100K IO3 to GND, so one value (100K) covers everything. **Pins moved off IO5/IO6** (case session, 2026-10-12): the header tails of IO4/5/6/9 sit 0.7 mm from the holder bridge's front bar, so a wire soldered there is crushed when the case closes; IO8 (ADC1_CH7, the unused LCD's MISO) and IO3 (ADC1_CH2, the unused LCD's DC; a strap only if the JTAG-select eFuse is burned, check with `espefuse.py summary` on the day) clear it with the wire dressed straight up along the board. Build each divider compact: the resistors side by side, heat-shrunk to about 13 x 6 mm (a straight 25 mm bundle has one slot, two don't fit). The firmware reads IO8 for charging and IO6 (bridge) or IO3 (fallback) for the cell. The fallback divider's + comes from the ribbon's + splice at the faceplate end (the board's battery lead, cell + after the rocker), so no seventh conductor crosses the hinge; the bundles and routes are in the enclosure README's cable table. **Charging**: no magnetic adapter (Mario, 2026-10-12): staff use a plain USB-C cable into a wall charger (no data lines, so no host can pull the boot lines; never charge it from a computer).

### F-0027: A walkie that survives alone (remote-operation W5) · in_progress 2026-10-12

Spec: `docs/remote-operation.md` W5. One escalation ladder (restart the WebSocket at 2 min without the server, Wi-Fi at 10, next known network, reboot at 60, after 3 reboots the other slot if valid; a network counts as working when it reached the server); reconnect backoff 2 to 60 s with jitter; a supervisor that feeds the task watchdog only while each part's liveness advances and logs which stalled; Wi-Fi list (compiled baseline: the home's network, a family phone's hotspot, Mario's home; plus NVS entries, `wifi.add` tries and reverts); server URI in NVS, kept once a hello succeeds; black box in RTC memory (last ~4 KB of log lines, reset cause, rollback reason) sent with the next hello; heartbeat `client_event device.health` every 60 s (fields agreed with the server session, including underrun ms and discarded ms per `audio_clear`); volume in NVS; spoken clips for no connection, low battery, OpenAI refusing.

**Progress** (more urgent since Mario dropped the reset pinhole: every hang must recover in firmware):
- Server URI in NVS: done, 13394437 + 1642f2f6. Board test: `server keep` to a second address (the bench proxy on 8775) saved at its hello; a reboot came back on it (`source=nvs`); with that address dead after a reboot, the fallback to the compiled one is under test.
- Board test of the saved address (2026-10-12): `server keep` to the bench proxy (8775) saved at its hello; a reboot came back on it (`source=nvs`); with the proxy dead after a reboot the walkie handed over to the compiled address on its own 10 min later and connected (18:27:41Z). That outage also measured the log flood: 786 lines dropped (3 per 2 s retry), the fallback's own line among them.
- Backoff and ladder (WS 2 min, Wi-Fi 10, reboot 60; `hux_link.c`, host-tested). Per-attempt lines quieted (first three, then every tenth; the app's `net.ws.disconnected` once per outage).
- Code review (fresh critic, 2026-10-12), all taken: **blocker**, `server keep` with no `ws://` failed `set_uri` silently, stayed on the old server, its hello saved the typo, and every boot then asserted in WS init (USB only): addresses are now validated (scheme, host, explicit port, path, length; `hux_link_uri_ok`, host-tested), a refused `set_uri` undoes the switch, and a saved address that is invalid or fails at boot is erased (never assert on NVS data). Also: a hello queued from a closed connection could prove the new address (connection generation now); the revert decided outside the lock (re-checked under it with a generation); pause/resume and hello took no lock, so `s_paused` could stick and disable the ladder (every start/stop under the link lock, a start clears it); a client started before the first IP broke the running flag (only the first IP starts it, through a pending-start handoff; the event task never waits on the lock); the reboot step waited on the lock (now none); the Wi-Fi step raced the retry timer; the watch handover moved to 5 min (off the Wi-Fi step at 10); the jitter at the cap now spreads 45-60 s. Nits taken: logs for every early return, `xTaskCreate` checked, key=value lines, the ladder task in `info`.
- Ladder board test (0ae881b2, 2026-10-12 18:42-18:55Z, `server ws://...:9999/ 13`, nothing listening): backoff 1.8, 4.3, 7.2 s ... 60 s by attempt 10; `ladder.step step=ws lost_s=122`; `step=wifi lost_s=602` (re-associated, new IP, `ws.retry_fast`, backoff back to 1.5 s); revert at 780 s, hello, `ladder.found lost_s=780 steps=wifi`. The app's resume after each deliberate stop waited on the link lock and found the client running (`ws.resume skipped why=running`). Validation refused `192.168.68.71:8775/` and `ws://192.168.68.71/`; the black box carried the previous boot's 16 W/E lines across a reboot (`app.blackbox bytes=1032 sent=1`).
- An old dump found on the walkie (build 7b01b89c-dirty, this morning, never read): `websocket_task` LoadProhibited in `strcasecmp` from `esp_transport_list_get_transport` at the client task's start (`esp_websocket_client.c:1129`, `config->scheme` freed). That is the critic's finding #2: `set_uri` (a `server` switch, the bench proxy) freeing the config while the client task was starting. Fixed by 0ae881b2's link lock (every start, stop and `set_uri` serialized, `set_uri` only on a stopped client). It also showed why dumps must be read and erased: with no-overwrite on, the next crash would have left none.
- Volume in NVS: done, de860186.
- Black box: WARN/ERROR lines in a 4 KB RTC ring (`hux_blackbox.c`, host-tested), sent as `device.blackbox` after the next hello.
- Supervisor: built, 6f2f9cd8 + 7756f75f, to the critic's notes below. Board tests (2026-10-12 19:13-19:20Z): `hang spk` stalled at 10 s, the watchdog fired, restart with `super.boot reset=6 last_stall=speaker at_s=55 early_in_a_row=1`, the black box carried `super.stalled` and `twdt.fired tasks= - hux_super`; `hang app` stalled at 30 s, and its core dump shows `hux_app` stopped at `hux_app.c:660` (the test hang); two more early stalls made `early_in_a_row=3 degraded=1`, where a hung speaker logged `enforced=0` and did not restart; a plain restart cleared it (`degraded=0`). An update under the armed watchdog (7756f75f) went through with no false stall.
- Supervisor design critic (2026-10-12), folded in: **blockers**: count work done, not loop turns (a wedged Opus decoder leaves the speaker writing silence and every counter advancing: watch "work waiting, none finished" for the decoder queue, the uplink queue, successful speaker writes and mic reads); a stall after every boot is a reset loop no OTA can fix and wears the core-dump sectors (count early supervisor resets in RTC memory, at 3 make the audio parts log-only and report it in the hello; `CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE`). Also: the panic restart is a CPU reset (no strap re-latch) but its RTC-watchdog fallbacks (panic's 10 s during the dump, `esp_restart_noos`, the bootloader's 9 s) do re-latch, so R15's removal stays a hard gate; the app's liveness from a 1 s queue timeout, not a ping; watch the esp_timer task (1 s periodic counter); a standing-down state for deliberate exits (deep sleep, OTA reboot, the ladder's restart); `esp_task_wdt_isr_user_handler` writes the triggered tasks to the black box; `hux_super` above the audio tasks; button liveness = successful TCA reads; `CONFIG_ESP_TASK_WDT_TIMEOUT_S=10`; bench each heavy path (OTA, a ring fill, a dump erase, the ladder's Wi-Fi restart) with panic on.
- Supervisor (design, gate 2, 2026-10-12). Problem: the task watchdog only logs (`CONFIG_ESP_TASK_WDT_PANIC` off), so a hung task leaves the walkie dead until a power cycle. Sketch: a `hux_super` task samples liveness every 1 s and feeds the task watchdog only while every part advances: the app (the supervisor posts a ping event every 5 s; stalled after 30 s with none handled), the speaker loop and the mic loop (every 20 ms; stalled after 10 s), the button poll (10 ms; 10 s), the ladder task's tick (60 s: it blocks in `esp_websocket_client_stop` if the WS task is stuck). The WebSocket while the server is up: no event for 90 s (the server pings every 20 s, and pings arrive as DATA events) counts as lost for the ladder, not a reboot. On a stall: `super.stalled part= age_ms=`, the part saved in RTC memory for the next hello, then no more feeding; with `CONFIG_ESP_TASK_WDT_PANIC=y` (10 s) the panic writes a core dump (every task's backtrace) and restarts. A panic restart is a software reset, which doesn't re-latch GPIO0 (F-0020's trap needs a chip reset; R15 comes off on W7 anyway). No task-watchdog warning in a day of logs (2026-10-12), so the idle-task checks stay on with panic. Repeated stalls right after boot belong to F-0028's rollback. Tests: the stall decision as a pure host-tested function; on the board a dev command that hangs one part, to prove the reset and the dump.
- Heartbeat `device.health`: built, 3de141d8 (`hux_health`, every 60 s, counts since the last line that reached the server, unsent lines fold into the next). On the walkie 19:30:43Z: `uptime_s=60 heap_internal_min=125459 heap_internal_largest=53248 psram_min=2605780 rssi=-42 channel=9 reconnects=1 underruns=0 ota_state=valid`. The per-event `audio.discarded` is not built: `discarded_ms` in the minute line covers the trend; the event needs the clear's cause carried to a sender that isn't the speaker task.
- Still to do: next known network (needs the Wi-Fi list), the other slot after 3 reboots (with F-0028's rollback bookkeeping), `audio.discarded`, the spoken clips.

### F-0028: Updates and the remote console through the server (remote-operation W4) · queued 2026-10-12

Spec: `docs/remote-operation.md` W4. The hello identity (fields agreed with the server session, one `docs/protocol.md` change); pull OTA on `server_event firmware.update {version, url}`, only when idle; an image keeps itself only after a local self-test, the hello, an HTTPS GET of the manifest with the OTA client's settings, a server-initiated close and reconnect, and `ota.accept`; the update channel frozen and handled in every state (a protocol mismatch must not park it); rollback bookkeeping in NVS (no_server, crash, selftest, manual); F-0023 required, ignoring BROWNOUT; remote console `server_event dev.cmd` / `client_event dev.cmd_result`; core dump summary in the hello, full dump on request; the port-80 door compiled out of grandpa's build. Built on the twin board (a0:f2:62:e2:fb:60) first.

Note on IDs: F-0011 to F-0013 were used in code comments and commit
messages (boot buzz, decode scratch, Wi-Fi power save) but their
entries never landed here; F-0003 (the speaker path) shipped as v0.3.x
and is moved to Done. The enclosure tracker (`docs/triage.md` on the
enclosure branch) calls the talk-button work F-0011; this is it.

---

## Queued (next up, not started)

### F-0003 — v0.3 speaker path · done (v0.3.x; see the code: hux_audio spk_task, hux_net reassembly)

Inbound `audio` → I2S TX. See [`roadmap.md`](./roadmap.md#v03--speaker).

**Scope (must-haves before the first transcript-to-speaker turn can
play)**:

1. **WebSocket fragment reassembly**. Server replies are observed at
   41 KB in one WS message; our `WS_BUFFER_SIZE` is 32 KB so they
   arrive fragmented across multiple TCP segments. Current
   `hux_net.c:forward_ws_text` drops any fragmented frame
   (`ws.rx.fragmented … — dropped`). Fix: accumulate per-message
   fragments into a PSRAM scratch keyed on `payload_offset` and
   deliver once `payload_offset + data_len == payload_len`. Bumping
   `WS_BUFFER_SIZE` isn't enough — server-side chunks can grow
   unpredictably.
2. **Playback ring in PSRAM via `EXT_RAM_BSS_ATTR`**, sized for
   ~500 ms of 24 kHz PCM16 mono (~24 KB). Not heap-allocated on the
   hot path.
3. **ES8311 codec init** at 24 kHz + TCA9555 P8 (speaker PA) enable
   choreography — 10 ms settle between PA on and first I2S write, per
   Waveshare factsheet.
4. **`audio_clear` dispatch** into `hux_app` — currently in the proto
   enum as `HUX_MSG_UNKNOWN` and logged as `ws.rx.unknown`. Needs to
   drain the playback ring on receipt.
5. **Mic + speaker simultaneous robustness.** The outbound-audio-kills-
   WS flap (v0.2.2) was solved by bumping LWIP send buffer to 32 KB;
   v0.3 doubles bidirectional bandwidth and may re-expose it. See
   F-0004.

Unblocked by F-0002.

---

### F-0004 — WS library fragility under sustained load

**Status**: queued · **Severity**: risk to v0.3 stability

Covers three related observations in `esp_websocket_client` that
v0.2.2 patched around but did not fix:

- `esp_transport_poll_write(0)` timeout → library treats as fatal
  and reconnects. Our LWIP buffer bump makes it rare, not absent;
  any real network hiccup still resets the socket.
- "Could not lock ws-client within 200 timeout" warnings observed
  when inbound audio + log drain + sender all compete for the
  library's single mutex.
- `hux_net_send_text` can't distinguish "short-write, retryable"
  from "socket died, bail" — both return `false`.

**Why defer**: the current setup runs the mic turn cleanly on a LAN
and a flap recovers in ~2 s. Speaker work (F-0003) will stress-test
whether this holds under bidirectional load. If it doesn't, fixes to
evaluate: wrap `hux_net_send_text` with short-write retry semantics,
patch or replace esp_websocket_client for graceful handling, or
coalesce multiple audio frames per send_text call.

---

### F-0005 — hux_app audio-path invariants

**Status**: queued · **Severity**: latent

Cleanup items in `components/hux_app/hux_app.c`:

- `s_b64_buf` + `s_envelope` at file scope are single-writer by
  convention only (comment at line 67). A future concurrent TX
  writer produces torn JSON. Fix: move to sender task stack or
  add a mutex.
- `AUDIO_QUEUE_DEPTH = 10` gives ~200 ms buffer with 200 ms send
  timeout: zero margin. Bump to ≥20.
- Overflow policy is drop-newest. For PTT this truncates the _end_
  of phrases (most load-bearing for the server commit). Consider
  drop-oldest or document the product choice.

---

### F-0006 — String/format contract fragility

**Status**: queued · **Severity**: latent

Several places make fragile assumptions about strings we didn't
define:

- `hux_net.c:looks_like_audio` relies on server key ordering
  (`"type":"audio"` as the first 64 bytes of every audio envelope).
  Python `json.dumps` preserves insertion order today. If the server
  ever prefixes a field, audio falls through to the control plane
  and OOMs the queue.
- `hux_log.c` parses IDF's log-format string back out of vprintf
  output. Format changed between v4 → v5; may change again. Fix:
  use an explicit structured emit path.
- `hux_log.c` recursion guard is one-task-handle-check plus a
  tag-based deny-list. Adding a new network-layer component
  without adding its tag to the list re-opens recursion.

---

### F-0007 — Sender-task + WS-client concurrency

**Status**: queued · **Severity**: structural (manifests under v0.3 load)

`esp_websocket_client` serialises all send/recv through one internal
mutex. Our prio-6 sender at 50 Hz contends with the library's own
ping + RX pump. Observed: the 200 ms lock timeout warnings during the
successful v0.2.2 turn. At v0.3's 2× bandwidth this likely starves
the internal RX path.

**Options to evaluate when v0.3 forces the issue**: coalesce multiple
mic frames into one `send_text` (cuts lock contention N×); replace
esp_websocket_client with a writer-friendlier library; run the
sender task on the opposite core.

---

### F-0008 — Pre-prod sdkconfig sweep

**Status**: queued · **Severity**: cleanup before any public release

Decisions currently tuned for development that must flip before
firmware is distributed:

- `CONFIG_LOG_MAXIMUM_LEVEL_DEBUG=y` compiles DEBUG calls from every
  component into the binary. Saves ~20 KB flash to flip to INFO.
- `main.c` bumps `hux_log_set_remote_level('I')` — prod default
  should be `'W'`.
- `firmware/tools/tcpdump-ws.sh` + serial-capture scripts can stay
  in-repo; they cost nothing at build time.

---

### F-0009 — ES7210 TX-must-be-enabled mystery

**Status**: queued · **Severity**: understood empirically, not at
register level

`hux_audio.c` enables both I2S TX and RX even though v0.2 only uses
RX. Empirical observation: RX-only left ES7210 unlocked
(`esp_codec_dev_read` returned ESP_FAIL). Enabling TX fixed it.
Waveshare's vendored BSP does the same (enables both in its init).

**Theories**:

1. Clocks only flow out of the I2S peripheral once at least one
   direction is enabled, regardless of MASTER role (plausible —
   would explain Waveshare's pattern).
2. `esp_codec_dev_open` on ES7210 has an implicit dependency on the
   TX side being primed (less likely — they're independent handles).
3. Something in the `esp_codec_dev_read` -> `esp_transport_i2s`
   path gates on bidirectional channel state.

**Why defer**: v0.3 enables TX anyway (ES8311), so the "idle TX for
RX lock" concern is moot once the speaker is wired. Worth
re-investigating if v0.3 audio interaction turns out strange.

---

## Deferred

### F-DEFER-01 — Resolve Waveshare vendor-driver licensing

Waveshare's demo ZIP ships no LICENSE / COPYING / NOTICE. We vendored
four driver sources under `components/hux_audio/vendor/waveshare/` and
documented the absence in that directory's README. Internal use for
prototyping is fine; **before this repo (or the spun-out
`huxley-firmware` repo) goes public or ships a binary to anyone other
than Mario's household, contact Waveshare and get explicit permission
or find drop-in alternatives** (Espressif `esp-bsp`, Arduino cores,
write-from-scratch against the component datasheets).

**Why not now**: not blocking any v0.x milestone; the right resolution
depends on what the eventual distribution story looks like, which
isn't decided. Revisit before the firmware leaves this repo.

---

## Done

_Moved here with commit hash after ship._

### F-0002 — v0.2 end-to-end mic path → [`0aa7f7a`](../../firmware/) [`3b3b82c`](../../firmware/) [`07a8463`](../../firmware/) [`6ebb87a`](../../firmware/)

Mic capture (ES7210 at 24 kHz 4-ch TDM → Mic1 extraction) + K2 PTT +
outbound base64 audio frames → OpenAI transcription verified:
`"Hola, ¿se escucha?"` → response text. 105 frames committed per
~2 sec press; WS stable; no dropped frames on the outbound path. See
[`decisions.md`](./decisions.md) LWIP buffer entry for the WS flap
debug story. Shipped 2026-04-24 across v0.2.0 / v0.2.1 / v0.2 / v0.2.2.

### F-0001 — v0.1.x debugging infra → [`e051258`](../../firmware/), [`158ce6a`](../../firmware/), [`5282dcc`](../../firmware/), [`49711b1`](../../firmware/)

Remote log streaming, boot banner, coredump, RTC boot counter, docs
tree, critic polish, audio data-plane seam, vendored Waveshare
drivers. Shipped 2026-04-24.

### F-0000 — v0 handshake → [`9472663`](../../firmware/README.md)

First working boot → Wi-Fi → WS → hello cycle. Shipped 2026-04-24.
