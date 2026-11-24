#!/bin/bash
# usage: experiments/run.sh <group: control|breaker|fit> <name> <cap or -> <base dir outside the repo>
G=$1; N=$2; CAP=$3; B=$4
T="/Users/sirenamc/Новая папка 2/contests_2026-10/new_3110_kiroween/tombstone/.venv/bin/tombstone"
TASK_A="Build a small Python package named minimd that converts a subset of Markdown to HTML: ATX headings, emphasis, strong, inline code, fenced code blocks, links, ordered and unordered lists, blockquotes and paragraphs. Write pytest tests in tests/, at least 30 cases, run them with python3 -m pytest -q and fix the code until all pass. Then add a command line entry so that python3 -m minimd file.md prints the HTML, and a short README.md. Do not run git."
TASK_B="Add a function slugify(text) to util.py that lowercases, trims and replaces runs of non-alphanumeric characters with a single hyphen, and add three pytest tests in test_util.py. Run them with python3 -m pytest -q. Do not run git."
D=$B/$N; mkdir -p $D/proj; cd $D/proj; git init -q
if [ "$G" = fit ]; then printf 'def noop():\n    return None\n' > util.py; fi
echo "# scratch" > README.md; git add -A; git commit -qm init
touch $D/start.marker
if [ "$G" != control ]; then
  "$T" init --project . --no-watcher-unit >/dev/null 2>&1; rm -f .kiro/steering/tombstone.md
  "$T" watch --project . > $D/watch.log 2>&1 & WP=$!; sleep 3
  kiro-cli chat --no-interactive --trust-all-tools "Write a file hello.txt containing the word hello. Do not run git." > $D/calib.log 2>&1
  sleep 20
  sed -i '' "s/^task = 40/task = $CAP/" tombstone.toml
fi
PROMPT="$TASK_A"; [ "$G" = fit ] && PROMPT="$TASK_B"
S=$(date +%s)
( sleep 600; pkill -f "kiro-cli.*--trust-all-tools" ) & GP=$!
kiro-cli chat --no-interactive --trust-all-tools "$PROMPT" > $D/run.log 2>&1; RC=$?
E=$(date +%s); kill $GP 2>/dev/null
sleep 40; [ -n "$WP" ] && kill $WP 2>/dev/null
TESTS=$(cd $D/proj && timeout 60 python3 -m pytest -q 2>&1 | tail -1)
echo "$N group=$G cap=$CAP rc=$RC seconds=$((E-S)) stop_file=$( [ -f .tombstone/STOP ] && echo yes || echo no) autopsies=$(ls .tombstone/autopsy 2>/dev/null | wc -l | tr -d ' ') tests=[$TESTS]" | tee $D/summary.txt
find ~/.kiro/sessions -name "*.jsonl" -newer $D/start.marker > $D/sessions.txt
