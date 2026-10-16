# Admin switcher redesign

Design canvas (private): https://claude.ai/artifact/XLfTb2qG3Kya9aZadyKVJ4
Layout A (status strip on top) is the chosen design.

## Who and when

- One operator running the show alone, live, on a laptop next to OBS (works down to ~900px wide).
- Everything on one page. No separate setup tab.
- Dark mode.

## OBS output

- One story-screen URL for every tournament and screen: `/live`. It shows whatever was last pushed with GO, with a quick fade.
- `/live` keeps the green `#00ff00` background.
- The per-tournament `/screen/<slug>/...` URLs go away (never deployed).
- VS bar stays as it is today: `/vsbar` and `/vsbar/<ring>`, ring set per tournament on the admin page.
- `/current_matches`, `/filtered_matches` and the JSON feeds stay as they are and share the same cached data.
- Brackets (ported from Brackinator) are their own OBS source, not part of `/live`, at the same path as in Brackinator: `/<challonge id>`. Named routes win over it in Flask, so Add must refuse IDs that clash with them (`admin`, `live`, `vsbar`, ...).

## Tournament rows

- One row per tournament, reorderable with up/down buttons, removable (with a confirm).
- Each row: name, Challonge ID, status (In progress / Not started), VS bar ring.
- Three screens per row, each with its own GO button:
  - Overview: no settings.
  - Upset watch and Elimination: an always-visible dropdown of at most 3 suggested matches.
- Dropdown labels: `Robot1/Robot2 seed1/seed2 round`, e.g. `Gremlin/Hexcavator 14/3 W3`. The auto pick is first and starts with `*`.
- No matches ready: "Nothing ready", GO disabled.

## Going live

- Nothing on air changes until GO is pressed. Changing the dropdown on the live screen marks it "Changed · press GO".
- The on-air screen and its row are highlighted; the top strip shows what's on air.
- When the bracket moves on, the dropdown re-ranks its suggestions but what's on air stays put until GO.
- If the fight that's on air finishes, `/live` falls back to that tournament's Overview rather than picking a different fight on its own.
- No clear/blank button: taking it off air is done in the video switcher.

## Status strip (top)

- Queries active/paused, auto-pause countdown, Pause/Resume, Challonge call count, last change.
- What's on air.
- The full `/live` URL with a Copy button.

## Bottom of the page

- Add tournament (Challonge ID or URL, optional ring).
- Bracket colours: per tournament background, title and round header (from Brackinator's display settings).
- All URLs: OBS sources, brackets, data feeds and admin, each with the full URL and a Copy button. VS bar URLs follow the rings in use.

## Behaviour

- Changes save in place; no full-page reloads, no 30-second auto-reload.
- The admin page's own polling keeps the data fresh; going live costs no extra Challonge requests.
- What's on air, row order and dropdown picks are saved with the tournament list in Cloud Storage, so everything can be set up before the event and survives a restart.

## Build notes

- Brackinator port lives in `bracket_view.py` (kept close to Brackinator's code) with `templates/tournament.html` and `match_container.html`. It reads the shared `("bracket", slug)` cache instead of calling Challonge on every page load. Colours are per tournament in tournaments.json (`display`), default background chroma green `#00ff00`. Not ported: Brackinator's admin page, `/raw/<slug>`, CORS and its unused templates.
