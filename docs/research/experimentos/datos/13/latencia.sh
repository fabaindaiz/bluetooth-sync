#!/bin/bash
# One latency run: recorder on the test sink's monitor (4 ch), player into aurasync_poc,
# plus a manual link player FL -> test sink AUX3 (the reference). Verifies routing.
S=$1; OUT=$2
pw-record --target aurasync_poc_test -P '{ node.name=poc-test-recorder stream.capture.sink=true node.dont-fallback=true node.dont-reconnect=true node.dont-move=true }' --rate 48000 --channels 4 --channel-map AUX0,AUX1,AUX2,AUX3 --format f32 "$OUT" &
REC=$!
sleep 1
pw-play --target aurasync_poc -P '{ node.name=poc-test-player node.dont-fallback=true node.dont-reconnect=true node.dont-move=true }' "$S/clicks.wav" &
PLAY=$!
for i in $(seq 20); do pw-link -o | grep -q '^poc-test-player:output_FL' && break; sleep 0.05; done
pw-link poc-test-player:output_FL aurasync_poc_test:playback_AUX3
sleep 0.3
L=$(pw-link -l)
echo "$L" | grep -A3 -E '^poc-test-(player|recorder)'
if echo "$L" | grep -A3 '^poc-test-player' | grep -E '\|->' | grep -vqE 'aurasync_poc:|aurasync_poc_test:playback_AUX3'; then
  echo "MISROUTE: killing player"; kill $PLAY; kill $REC; exit 1
fi
if echo "$L" | grep -A4 '^poc-test-recorder' | grep -E '\|<-' | grep -vq 'aurasync_poc_test:monitor_'; then
  echo "MISROUTE recorder: killing"; kill $PLAY; kill $REC; exit 1
fi
wait $PLAY
sleep 1
kill -INT $REC; wait $REC
echo done
