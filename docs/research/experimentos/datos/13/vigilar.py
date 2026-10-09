"""Safety watch: prints a line only when something about the user's audio or the test player is off."""
import subprocess, time
def links():
    out, cur, res = subprocess.run(["pw-link", "-l"], capture_output=True, text=True).stdout.splitlines(), None, {}
    for l in out:
        if not l.startswith(" "):
            cur = l.strip(); res.setdefault(cur, [])
        elif "|->" in l and cur:
            res[cur].append(l.split("|->")[1].strip())
    return res
while True:
    d = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True).stdout.strip()
    if d != "aurasync":
        print("ALERT default sink:", d, flush=True)
    L = links()
    if "aurasync:playback_FL" not in L.get("spotify:output_FL", []):
        print("NOTE spotify not linked to aurasync:", L.get("spotify:output_FL"), flush=True)
    for port, dst in L.items():
        if port.startswith(("poc-test-player:", "aurasync_poc_output:")):
            bad = [x for x in dst if not x.startswith(("aurasync_poc:", "aurasync_poc_test:"))]
            if bad:
                print("ALERT", port, "->", bad, flush=True)
    time.sleep(3)
