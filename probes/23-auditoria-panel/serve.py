"""The simulated service alone (for Lighthouse): playing, a virtual speaker, monitor on. Stops on SIGTERM/SIGINT."""

import signal
import sys
import threading

from harness import TOKEN, service

svc, tmp = service()
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
signal.signal(signal.SIGINT, lambda *_: stop.set())
try:
    svc.command("speaker_add_virtual")
    svc.command("start")
    svc.command("monitor_set", mode="binaural", target="simulated_headphones")
    print(f"{svc.url}/?t={TOKEN}", flush=True)
    stop.wait()
finally:
    svc.stop()
    tmp.cleanup()
    print("stopped", flush=True)
    sys.exit(0)
