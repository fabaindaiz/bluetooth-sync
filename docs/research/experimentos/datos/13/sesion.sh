#!/bin/bash
# One B session: PoC + noise player into aurasync_poc for DUR seconds, optional nice-19 CPU load.
# Usage: session.sh LABEL DUR LOAD(0|N) [extra poc args...]
LABEL=$1; DUR=$2; LOAD=$3; shift 3
S=/tmp/claude-1000/-home-fadiaz-Desktop-Project-bluetooth-sync/d2684a5c-0f64-4611-8397-186d8e93e4d4/scratchpad
D=/home/fadiaz/Desktop/Project/bluetooth-sync/docs/research/experimentos/datos/13
P=/home/fadiaz/Desktop/Project/bluetooth-sync/probes/17-e-s-nativa-rust/target/release/poc-pw
cd $S/poc
echo "== $LABEL start $(date '+%F %T') dur=$DUR load=$LOAD args=$*"
pw-top -b -n 3 2>&1 | grep -E 'QUANT|aurasync|bluez_output|spotify|pw-play' | tail -9 > $D/$LABEL-pwtop-servicio-inicio.txt
$P --config $D/poc-config.json --target aurasync_poc_test --node-name aurasync_poc --log $D/$LABEL.jsonl "$@" < /dev/null > $S/poc/$LABEL-stdout.txt 2> $S/poc/$LABEL-stderr.txt &
POC=$!
sleep 1.5
pw-cli ls Node | grep -q '"aurasync_poc"' || { echo "no aurasync_poc node; abort"; kill -INT $POC; exit 1; }
pw-play --target aurasync_poc -P '{ node.name=poc-test-player node.dont-fallback=true node.dont-reconnect=true node.dont-move=true }' $S/poc/noise.wav &
PLAY=$!
sleep 1
L=$(pw-link -l)
echo "$L" | grep -A2 '^poc-test-player'
if ! echo "$L" | grep -A1 '^poc-test-player:output_FL' | grep -q 'aurasync_poc:playback_FL' || echo "$L" | grep -A3 '^poc-test-player' | grep -E '\|->' | grep -vq 'aurasync_poc:'; then
  echo "MISROUTE: abort"; kill $PLAY; kill -INT $POC; exit 1
fi
echo "routing ok; default sink: $(pactl get-default-sink)"
ps -L -o tid,cls,rtprio,ni,comm -p $POC > $D/$LABEL-hilos.txt
LOADPIDS=""
if [ "$LOAD" != 0 ]; then
  for i in $(seq $LOAD); do nice -n 19 sh -c 'while :; do :; done' & LOADPIDS="$LOADPIDS $!"; done
  echo "load pids:$LOADPIDS"
fi
sleep 5
pw-top -b -n 3 2>&1 | grep -E 'QUANT|aurasync_poc|poc-test' | tail -5 > $D/$LABEL-pwtop-inicio.txt
sleep $((DUR - 10))
pw-top -b -n 3 2>&1 | grep -E 'QUANT|aurasync_poc|poc-test' | tail -5 > $D/$LABEL-pwtop-fin.txt
uptime > $D/$LABEL-uptime.txt
[ -n "$LOADPIDS" ] && kill $LOADPIDS
sleep 2
pw-top -b -n 3 2>&1 | grep -E 'QUANT|aurasync|bluez_output|spotify|pw-play' | tail -9 > $D/$LABEL-pwtop-servicio-fin.txt
kill $PLAY
kill -INT $POC; wait $POC
echo "== $LABEL end $(date '+%F %T'); default sink: $(pactl get-default-sink)"
