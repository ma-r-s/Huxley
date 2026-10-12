# A look inside a running board without resetting it: the WebSocket's TCP
# control block and every task's backtrace, then resume. For a stall or a
# board that looks alive but isn't (F-0015, F-0016). See tools/README.md:
#   openocd -c "set ESP_FLASH_SIZE 0" -f board/esp32s3-builtin.cfg &
#   xtensa-esp32s3-elf-gdb -batch -x tools/jtag_snapshot.gdb build/huxley-firmware.elf
# Reads only: never `call` a function here (an inferior call on a halted
# ESP32 can corrupt it; the board double-faulted on resume once).
set pagination off
set confirm off
target extended-remote :3333
set $p = tcp_active_pcbs
if $p != 0
  printf "TCP state=%d rcv_wnd=%u snd_wnd=%u snd_buf=%u nrtx=%d keep_cnt_sent=%d refused=%p\n", $p->state, $p->rcv_wnd, $p->snd_wnd, $p->snd_buf, $p->nrtx, $p->keep_cnt_sent, $p->refused_data
  set $s = $p->ooseq
  set $n = 0
  while $s != 0
    set $n = $n + 1
    set $s = $s->next
  end
  printf "TCP out-of-order segments queued: %d\n", $n
else
  printf "no active TCP connection\n"
end
info threads
thread apply all bt 12
mon resume
detach
