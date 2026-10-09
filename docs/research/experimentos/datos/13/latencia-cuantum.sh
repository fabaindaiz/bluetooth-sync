#!/bin/bash
# Latency at a forced quantum: PoC with --latency Q/48000, two click runs, clean stop.
Q=$1
S=/tmp/claude-1000/-home-fadiaz-Desktop-Project-bluetooth-sync/d2684a5c-0f64-4611-8397-186d8e93e4d4/scratchpad
D=/home/fadiaz/Desktop/Project/bluetooth-sync/docs/research/experimentos/datos/13
P=/home/fadiaz/Desktop/Project/bluetooth-sync/probes/17-e-s-nativa-rust/target/release/poc-pw
$P --config $D/poc-config.json --target aurasync_poc_test --node-name aurasync_poc --latency $Q/48000 --log $D/L-q$Q.jsonl < /dev/null 2> $S/poc/L-q$Q-stderr.txt &
POC=$!
sleep 1.5
pw-cli ls Node | grep -q '"aurasync_poc"' || { echo "no node"; kill -INT $POC; exit 1; }
for i in 1 2; do
  $S/latency.sh $S/poc $S/poc/lat-q$Q-$i.wav | grep -E 'MISROUTE|done'
  python3 $S/analyze.py $S/poc/lat-q$Q-$i.wav | tee -a $D/latencia.jsonl | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["ref_clicks"], {k:(v["pairs"],v["values"]) for k,v in d.items() if k.startswith("AUX")})'
done
pw-top -b -n 2 2>&1 | grep -E 'QUANT|aurasync_poc' | tail -4
kill -INT $POC; wait $POC
tail -1 $D/L-q$Q.jsonl | python3 -c 'import json,sys; m=json.load(sys.stdin)["metrics"]; print("quantum", m["quantum"], "xruns", m["xruns"], "cb p999", m["callback"]["p999_us"], "ratio", m["callback_p999_of_min_quantum"])'
