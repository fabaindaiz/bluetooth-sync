#!/bin/bash
# P1: kill -9 the PoC while a player plays into it; the test sink, the player and the
# user's session must survive, the PoC nodes must vanish.
S=/tmp/claude-1000/-home-fadiaz-Desktop-Project-bluetooth-sync/d2684a5c-0f64-4611-8397-186d8e93e4d4/scratchpad
D=/home/fadiaz/Desktop/Project/bluetooth-sync/docs/research/experimentos/datos/13
P=/home/fadiaz/Desktop/Project/bluetooth-sync/probes/17-e-s-nativa-rust/target/release/poc-pw
$P --config $D/poc-config.json --target aurasync_poc_test --node-name aurasync_poc --log $D/K-kill9.jsonl < /dev/null 2> $S/poc/K-stderr.txt &
POC=$!
sleep 1.5
pw-cli ls Node | grep -q '"aurasync_poc"' || { echo "no node"; kill -INT $POC; exit 1; }
pw-play --target aurasync_poc -P '{ node.name=poc-test-player node.dont-fallback=true node.dont-reconnect=true node.dont-move=true }' $S/poc/noise.wav &
PLAY=$!
sleep 3
echo "--- before kill -9 ($(date +%T)), poc pid $POC"
pw-link -l | grep -A1 -E '^(poc-test-player|aurasync_poc_output):'
kill -9 $POC
for i in $(seq 10); do
  if pw-link -l | grep -A1 '^poc-test-player' | grep -E '\|->' | grep -q .; then
    echo "player linked after kill at check $i:"; pw-link -l | grep -A1 '^poc-test-player'
    pw-link -l | grep -A1 '^poc-test-player' | grep -E '\|->' | grep -vqE 'aurasync_poc' && { echo "MOVED ELSEWHERE: killing player"; kill $PLAY; }
  fi
  sleep 0.2
done
echo "--- 2 s after kill -9"
echo "poc alive? $(kill -0 $POC 2>/dev/null && echo yes || echo no)"
echo "poc nodes: $(pw-cli ls Node | grep -cE '"aurasync_poc(_output)?"')"
echo "test sink: $(pactl list short sinks | grep -c aurasync_poc_test)"
echo "player alive? $(kill -0 $PLAY 2>/dev/null && echo yes || echo no)"
echo "player links:"; pw-link -l | grep -A2 '^poc-test-player' || echo "  (player has no links / no node)"
echo "default sink: $(pactl get-default-sink)"
pw-link -l | grep -A1 -E '^(spotify|pw-play):output_FL'
kill $PLAY 2>/dev/null; wait $PLAY 2>/dev/null
echo "--- after stopping the player: poc-test nodes $(pw-cli ls Node | grep -c poc-test)"
