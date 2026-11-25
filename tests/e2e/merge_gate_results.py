"""Merge the results.json files of the install gate's shards into one summary.

    python tests/e2e/merge_gate_results.py --planned planned.json RESULTS_JSON...

Prints the markdown, appends it to $GITHUB_STEP_SUMMARY when set, and exits 0
when every planned chart passed, 1 otherwise (a shard that produced no
results.json leaves its charts "never reached").
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import install_gate  # noqa: E402


def merge(planned, shard_results):
    targets = planned["targets"]
    skipped = planned["skipped"]
    if not targets:
        text = "## Chart install gate\n\nNo chart selected; nothing installed.\n" + "".join(
            f"\n- skipped by name: `{name}` - {reason}"
            for name, reason in sorted(skipped.items()))
        return {"text": text, "ok": True}
    results = [record for shard in shard_results for record in shard.get("results", [])]
    notes = [note for shard in shard_results for note in shard.get("notes", [])]
    total = round(sum(item["seconds"] for shard in shard_results
                      for item in shard.get("timings", [])), 1)
    header = f"Sharded run: {len(planned['shards'])} shard(s)"
    text, ok = install_gate.render_summary(results, targets, skipped, notes, total, header)
    return {"text": text, "ok": ok}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planned", required=True, type=Path)
    parser.add_argument("results", nargs="*", type=Path)
    args = parser.parse_args()
    planned = json.loads(args.planned.read_text())
    shards = [json.loads(path.read_text()) for path in args.results]
    merged = merge(planned, shards)
    print(merged["text"], flush=True)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as handle:
            handle.write(merged["text"] + "\n")
    return 0 if merged["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
