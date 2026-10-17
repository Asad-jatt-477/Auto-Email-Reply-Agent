# Demo data (web/data)

The public demo (`web/index.html`) renders from two static JSON files, never from hardcoded numbers.

| File | Source | Contains |
|---|---|---|
| `web/data/metrics.json` | newest full-pipeline run in `evaluation/results/*/metrics.json` | accuracy, F1, confusion matrix, per-class table, latency |
| `web/data/stats.json` | read-only aggregate of `data/agent_state.db` | counts per action / source / language / category / day |

Neither file contains sender addresses, subjects, snippets, drafts or message ids. The exporter
checks every string against an allow-list and refuses to write if anything looks unsafe.

## Update workflow
```powershell
python evaluation/run_eval.py          # optional: new evaluation run (uses Groq tokens)
python scripts/export_demo_data.py     # regenerate web/data/*.json
git add web/data && git commit -m "Update demo data" && git push   # Vercel redeploys
```
Options: `--dry-run` (print only), `--skip-stats`, `--db`, `--results-dir`, `--out-dir`.

## Preview locally
`fetch()` does not work on `file://`, so serve the folder:
```powershell
python -m http.server -d web 8000      # then open http://localhost:8000
```
If `metrics.json` is missing or invalid the page shows an error card instead of empty boxes.
