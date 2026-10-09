import json, sys
lines = [json.loads(l) for l in open(sys.argv[1])]
iv = [d for d in lines if d.get("kind") == "interval"]
bad = [(d["uptime_s"], d["metrics"]["xruns"], d["metrics"]["graph_xrun_flags"], d["metrics"]["bad_buffer"], d.get("same_driver"))
       for d in iv if d["metrics"]["xruns"]["total"] or d["metrics"]["graph_xrun_flags"] or d["metrics"]["bad_buffer"] or d.get("same_driver") is not True]
sd = sorted({str(d.get("same_driver")) for d in iv})
drv = sorted({(d["capture"]["driver"] or {}).get("clock_id") for d in iv} | {(d["playback"]["driver"] or {}).get("clock_id") for d in iv}, key=str)
s = [d for d in lines if d.get("kind") == "summary"][-1]["metrics"]
delays = sorted({(d["playback"]["time"] or {}).get("delay") for d in iv}, key=str)
cdelays = sorted({(d["capture"]["time"] or {}).get("delay") for d in iv}, key=str)
print(json.dumps({"intervals": len(iv), "anomalous_intervals": bad[:10], "n_anomalous": len(bad), "same_driver_values": sd, "driver_clock_ids": drv,
  "callbacks": s["callbacks"], "quantum": s["quantum"], "xruns": s["xruns"], "graph_xrun_flags": s["graph_xrun_flags"], "bad_buffer": s["bad_buffer"],
  "trigger_failed": s["trigger_failed"], "rt_alloc_violations": s["rt_alloc_violations"],
  "callback": s["callback"], "callback_p999_of_min_quantum": s["callback_p999_of_min_quantum"], "period": s["period"], "wakeup": s["wakeup"],
  "playback_delay_frames": delays[:8], "capture_delay_frames": cdelays[:8]}, indent=1))
