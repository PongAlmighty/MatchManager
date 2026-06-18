---
name: Flask server requires manual restart after code changes
description: Why new routes/code edits don't take effect until the workflow is restarted in this repl
---

The Flask Server workflow runs with debug mode OFF, so there is no auto-reload.

**Symptom:** After adding/editing a route, hitting it returns a stale result (e.g. a brand-new route 404s) even though the code is correct.

**Why:** No reloader is active; the running process keeps serving the code it started with.

**How to apply:** After any edit to `main.py` (routes, config like `tournament_ids` / `RING_TOURNAMENTS`, logic), restart the `Flask Server` workflow before testing, otherwise live curl/browser checks reflect old code and you'll draw wrong conclusions.

Also note: each request re-fetches all tournaments from the Challonge API and is slow (~10-40s). Don't chain many curls in one short-timeout shell call — they time out. Test one route at a time with a generous `--max-time`.
