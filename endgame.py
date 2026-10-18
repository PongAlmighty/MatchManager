"""The end-of-tournament screen ("Road to the final") for elimination brackets.

Once 8 or fewer robots are left, the bracket page swaps the full bracket for this 1920x1080 screen:
the last rounds as a small bracket that fills in left to right, each winner's colour carried forward
on a ribbon, and a side column with how many are left and, from the last 3, the podium. Double
elimination puts the winners rounds on top and second chance underneath, both feeding the grand
final. build() returns boxes and ribbon paths with their pixel positions; the template only draws them.
The look matches the story screens (static/js/story_screen.js) and the design canvas it came from.
"""
from collections import defaultdict

import story

ENDGAME_AT = 8          # switch to this screen at this many robots left, or fewer
BOTTOM = 1005           # keep clear of the tournament name pill at the bottom of the page
RIGHT_X, RIGHT_W = 1420, 460

HEADER = {'bg': '#262626', 'fg': '#cccccc'}   # the "Match X" strip on top of a card
GOLD = {'bg': 'gold', 'fg': '#333333'}   # the fight marked underway, as on the other bracket pages
STRIPES = 'repeating-linear-gradient(135deg, rgba(44, 44, 44, 0.75) 0 14px, transparent 14px 28px)'
TBD = {'bg': '#2a2a2a', 'fg': '#cccccc', 'fw': 600}


def color_of(seed):
    """A robot's colour from its seed, the same formula as the story screens."""
    k = seed if seed and seed > 0 else 1
    h = (195 + ((k - 1) * 0.618034 % 1) * 220) % 360
    l = 30 if 15 < h < 60 else (32 if k % 2 else 40)
    return f'hsl({round(h)}, 62%, {l}%)'


def _band(x0, y0, h0, x1, y1, h1, fill):
    """A ribbon joining a box's right edge to another box's left edge, tucked under both."""
    x0 -= 6
    x1 += 6
    xm = (x0 + x1) / 2
    return {'fill': fill, 'd': f'M {x0} {y0} C {xm} {y0} {xm} {y1} {x1} {y1} L {x1} {y1 + h1} '
                               f'C {xm} {y1 + h1} {xm} {y0 + h0} {x0} {y0 + h0} Z'}


def _window(rounds, by_round):
    """Which rounds of one side to show: from the first round still being played to the end. Finished
    rounds are left off, so the space goes to what's still to come (who's out is in the side column).
    A side with everything played keeps its last round, which feeds the grand final."""
    open_rounds = [i for i, r in enumerate(rounds) if any(m['state'] != 'complete' for m in by_round[r])]
    return rounds[open_rounds[0] if open_rounds else len(rounds) - 1:]


class _Screen:
    def __init__(self, matches, participants, double, pill_bg):
        self.bracket = story.Bracket(matches, participants, 'double elimination' if double else 'single elimination')
        self.ms = matches
        self.by_id = {m['id']: m for m in matches}
        self.names = {p['id']: p['name'] for p in participants}
        self.seeds = {p['id']: p.get('seed') or 0 for p in participants}
        self.double = double
        self.pill_bg = pill_bg
        self.boxes, self.ribbons = [], []
        self.pos = {}   # match id -> (x, top, width, header height, row height)
        self.alive = set()   # robots still in, so a loser who isn't out yet isn't marked out

    # --- drawing helpers -------------------------------------------------------------------------

    def box(self, x, y, w, h, text='', extra='', bg='#2c2c2c', fg='#ffffff', fs=18, fw=700, xfs='0.8em',
            xfg='inherit', pad='0 12px', radius='6px', justify='space-between'):
        # Round the edges rather than the sizes, so stacked rows meet exactly instead of overlapping a pixel.
        left, top = round(x), round(y)
        w, h = round(x + w) - left, round(y + h) - top
        self.boxes.append({
            'text': text, 'extra': extra,
            'style': f'left:{left}px;top:{top}px;width:{w}px;height:{h}px;padding:{pad};'
                     f'border-radius:{radius};justify-content:{justify};background:{bg};color:{fg};'
                     f'font-size:{fs}px;font-weight:{fw}',
            'xstyle': f'font-size:{xfs};color:{xfg}',
        })

    def pill(self, x, y, w, text, h=36, fs=18):
        self.box(x, y, w, h, text, bg=self.pill_bg, fs=fs, justify='center')

    def name(self, pid):
        return self.names.get(pid, 'Unknown')

    def feeders(self, m):
        """Matches whose winner moves on into m, as (slot, match id)."""
        return [(k, m[f'player{n}_prereq_match_id']) for n, k in ((1, 'a'), (2, 'b'))
                if m.get(f'player{n}_prereq_match_id') and not m.get(f'player{n}_is_prereq_match_loser')]

    def label(self, m):
        """A match's name as /live gives it: round tag and play order, "LR7 · #66". The grand final
        reset has no play order number."""
        tag = self.bracket.round_short(m)
        order = m.get('suggested_play_order')
        return f'{tag} · #{order}' if order else f'{tag} reset' if tag == 'GF' else tag

    def slot_label(self, m, n):
        pre = m.get(f'player{n}_prereq_match_id')
        if not pre or pre not in self.by_id:
            return 'TBD'
        side = 'Loser' if m.get(f'player{n}_is_prereq_match_loser') else 'Winner'
        return f"{side} of {self.label(self.by_id[pre])}"

    # --- one match card ----------------------------------------------------------------------------

    def card(self, m, x, w, center, card_h, underway, note='', striped=False):
        hh = 30 if card_h >= 140 else 26 if card_h >= 100 else 22
        row = (card_h - hh) / 2
        top = center - card_h / 2
        fs = round(row / 3) if row >= 100 else 26 if row >= 64 else 22 if row >= 54 else 20 if row >= 44 else 16 if row >= 34 else 14
        if w < 200:
            fs -= 1
        # Shrink until the longest name fits beside its W/L mark (about 0.56em a character). An empty
        # slot's "Winner of LR7 · #65" is sized on its own, so it never shrinks a real name.
        def fit(size, text):
            return max(12, min(size, int((w - 54) / (0.56 * len(text))))) if text else size
        fs = fit(fs, max((self.name(m[f'player{n}_id']) for n in (1, 2) if m.get(f'player{n}_id')), key=len, default=''))
        style = GOLD if underway else HEADER
        self.box(x, top, w, hh, self.label(m), note, bg=style['bg'], fg=style['fg'],
                 fs=15 if hh > 26 else 13 if hh > 22 else 12, xfs='1em', radius='6px 6px 0 0')
        for n, radius in ((1, '0'), (2, '0 0 6px 6px')):
            y = top + hh + (n - 1) * row
            pid = m.get(f'player{n}_id')
            if not pid:
                self.box(x, y, w, row, self.slot_label(m, n), fs=fit(fs - 2, self.slot_label(m, n)), radius=radius, **TBD)
            elif underway:
                self.box(x, y, w, row, self.name(pid), bg=GOLD['bg'], fg=GOLD['fg'], fs=fs, radius=radius)
            elif m['state'] == 'complete' and pid == m.get('loser_id'):
                if pid in self.alive and striped:   # lost the grand final but a reset follows: still in it
                    self.box(x, y, w, row, self.name(pid), 'L', bg=f'{STRIPES}, {color_of(self.seeds.get(pid))}',
                             fs=fs, radius=radius)
                elif pid in self.alive:   # a first loss in double elim: one life left
                    self.box(x, y, w, row, self.name(pid), 'L', bg='#3a3a3a', fg='#e0e0e0', fw=600, fs=fs, radius=radius)
                else:
                    self.box(x, y, w, row, self.name(pid), 'out', bg='#262626', fg='#9a9a9a', fw=600, fs=fs, radius=radius)
            else:
                won = m['state'] == 'complete'
                extra = ('W' if self.double else 'won') if won else ('' if self.double or w < 300 else f'seed {self.seeds.get(pid)}')
                self.box(x, y, w, row, self.name(pid), extra, bg=color_of(self.seeds.get(pid)), fs=fs, radius=radius)
        self.pos[m['id']] = (x, top, w, hh, row)

    # --- one side of the bracket -------------------------------------------------------------------

    def place(self, cols, by_round, top, slot, card_h):
        """Centre of each shown match: the biggest round evenly spaced, later rounds between the
        matches that feed them, earlier (play-in) rounds level with the match they feed."""
        centers = {}
        shown = {m['id'] for r in cols for m in by_round[r]}
        k = max(range(len(cols)), key=lambda i: len(by_round[cols[i]]))

        def spread(ms, ys):
            n = len(ms)
            area = slot * max(len(by_round[cols[k]]), 1)
            ys = [y if y is not None else top + area / n * (i + 0.5) for i, y in enumerate(ys)]
            for i in range(1, n):   # never let two cards overlap
                ys[i] = max(ys[i], ys[i - 1] + card_h + 8)
            for m, y in zip(ms, ys):
                centers[m['id']] = y

        spread(by_round[cols[k]], [None] * len(by_round[cols[k]]))
        for j in range(k + 1, len(cols)):
            ys = []
            for m in by_round[cols[j]]:
                fed = [centers[f] for _, f in self.feeders(m) if f in centers]
                ys.append(sum(fed) / len(fed) if fed else None)
            spread(by_round[cols[j]], ys)
        for j in range(k - 1, -1, -1):
            ys = []
            for m in by_round[cols[j]]:
                target = next((t for t in self.ms if t['id'] in shown and m['id'] in [f for _, f in self.feeders(t)]), None)
                ys.append(centers.get(target['id']) if target else None)
            spread(by_round[cols[j]], ys)
        return centers

    def ribbons_into(self, m):
        """Ribbons from the shown matches feeding m: the winner's colour once decided, grey before."""
        if m['id'] not in self.pos:
            return
        x1, top1, _, hh1, row1 = self.pos[m['id']]
        for k, f in self.feeders(m):
            if f not in self.pos:
                continue
            fm = self.by_id[f]
            x0, top0, w0, hh0, row0 = self.pos[f]
            y1 = top1 + hh1 + (0 if k == 'a' else row1)
            if fm['state'] == 'complete' and fm.get('winner_id'):
                y0 = top0 + hh0 + (0 if fm['winner_id'] == fm.get('player1_id') else row0)
                self.ribbons.append(_band(x0 + w0, y0, row0, x1, y1, row1, color_of(self.seeds.get(fm['winner_id']))))
            else:
                self.ribbons.append(_band(x0 + w0, top0 + hh0, row0 * 2, x1, y1, row1, '#2c2c2c'))


def build(matches, participants, tournament_type, pill_bg='#2C2C2C'):
    """The end screen for an elimination bracket with 2 to ENDGAME_AT robots left, else None."""
    ttype = (tournament_type or '').lower()
    if 'elimination' not in ttype:
        return None
    double = 'double' in ttype
    ms = [m for m in matches if m.get('group_id') is None]
    if not ms:
        return None
    by_id = {m['id']: m for m in ms}

    lives = 2 if double else 1
    done = sorted((m for m in ms if m['state'] == 'complete' and m.get('loser_id')),
                  key=lambda m: (m.get('completed_at') or '', m['id']))
    losses = defaultdict(list)
    for m in done:
        losses[m['loser_id']].append(m)
    in_bracket = {m[k] for m in ms for k in ('player1_id', 'player2_id') if m.get(k)}
    alive = [p['id'] for p in sorted(participants, key=lambda p: p.get('seed') or 0)
             if p['id'] in in_bracket and len(losses[p['id']]) < lives]
    if not 2 <= len(alive) <= ENDGAME_AT:
        return None

    # The grand final takes the winners final winner against the second chance winner; its reset is
    # fed by the grand final twice. A third place match takes two losers.
    gf = reset = third = None
    if double:
        gf = next((m for m in ms if m['round'] > 0 and any(
            by_id.get(m.get(f'player{n}_prereq_match_id'), {}).get('round', 0) < 0 for n in (1, 2))), None)
        if gf:
            reset = next((m for m in ms if m is not gf and m.get('player1_prereq_match_id') == gf['id']
                          and m.get('player2_prereq_match_id') == gf['id']), None)
    else:
        third = next((m for m in ms if m['round'] > 0 and m.get('player1_is_prereq_match_loser')
                      and m.get('player2_is_prereq_match_loser')), None)
    special = {m['id'] for m in (gf, reset, third) if m}

    by_round = defaultdict(list)
    for m in ms:
        if m['id'] not in special:
            by_round[m['round']].append(m)
    w_rounds = sorted(r for r in by_round if r > 0)
    l_rounds = sorted((r for r in by_round if r < 0), key=abs)
    w_cols = _window(w_rounds, by_round) if w_rounds else []
    l_cols = _window(l_rounds, by_round) if l_rounds else []

    # The fight marked underway in Challonge turns gold.
    def underway(m):
        return bool(m['state'] == 'open' and m.get('underway_at') and m.get('player1_id') and m.get('player2_id'))

    def w_name(r):
        i = len(w_rounds) - 1 - w_rounds.index(r)
        if double:
            return ['Winners final', 'Winners semis'][i] if i < 2 else f'Winners R{r}'
        return ['Final', 'Semifinals', 'Quarterfinals'][i] if i < 3 else f'Round {r}'

    def l_name(r):
        return 'Second chance final' if r == l_rounds[-1] else f'Second chance R{abs(r)}'

    s = _Screen(ms, participants, double, pill_bg)
    s.alive = set(alive)

    def columns(cols, end, w, gap, names, header_y, top, slot, card_h, header_below=None):
        """Draw one side's rounds, w wide, the last one ending at x = end."""
        start = end - len(cols) * (w + gap) + gap
        centers = s.place(cols, by_round, top, slot, card_h)
        for i, r in enumerate(cols):
            x = start + i * (w + gap)
            s.pill(x, header_below if header_below is not None else header_y, w, names(r), 30, 15)
            for m in by_round[r]:
                s.card(m, x, w, centers[m['id']], card_h, underway(m), )
        return centers

    # Cards grow as matches run out (up to 176px tall in double elim, 170px in single), and the bracket is centred in the height
    # between the top of the screen and the tournament name.
    room = BOTTOM - 24
    reset_on = bool(double and gf and reset and reset.get('player1_id'))
    if reset_on:
        # The grand final is played and the reset decides it: the grand final takes the winners final's
        # place at round size, and the reset is the big match, both robots' ribbons running into it.
        cy = 24 + room / 2
        row = min(135, (room - 120) / 2)
        gh = 30 + 2 * row
        rx, rw = 610, 664   # where the grand final sits once one round is left on each side
        s.pill(rx, cy - gh / 2 - 58, rw, 'Grand final reset', 48, 24)
        s.card(reset, rx, rw, cy, gh, underway(reset))
        s.pill(40, cy - gh / 2 - 40, 384, 'Grand final', 30, 15)
        s.card(gf, 40, 384, cy, 176, underway(gf), striped=True)
        gx0, gtop, gw0, ghh, grow = s.pos[gf['id']]
        _, rtop, _, rhh, rrow = s.pos[reset['id']]
        for n in (1, 2):
            pid = reset.get(f'player{n}_id')
            if pid in (gf.get('player1_id'), gf.get('player2_id')):
                k = 0 if pid == gf.get('player1_id') else 1
                s.ribbons.append(_band(gx0 + gw0, gtop + ghh + k * grow, grow, rx, rtop + rhh + (n - 1) * rrow, rrow,
                                       color_of(s.seeds.get(pid))))
    elif double:
        n_w = max((len(by_round[r]) for r in w_cols), default=1)
        n_l = max((len(by_round[r]) for r in l_cols), default=1)
        chrome = 40 + 33 + 36   # headers above, the divider, headers below
        slot = min(240, (room - chrome) / (n_w + n_l))
        card_h = min(176, slot - 14)
        y0 = 24 + (room - chrome - (n_w + n_l) * slot) / 2
        w_top = y0 + 40
        # Both sides end level, the side with more rounds starting at the left edge. Rounds grow to 384px
        # wide, always leaving the grand final at least 230px.
        n = max(len(w_cols), len(l_cols), 1)
        w = min(384, (1030 - (n - 1) * 50) / n)
        end = 40 + n * w + (n - 1) * 50
        w_centers = columns(w_cols, end, w, 50, w_name, y0, w_top, slot, card_h)
        divider = w_top + n_w * slot + 8
        l_top = divider + 25
        l_centers = columns(l_cols, end, w, 50, l_name, None, l_top, slot, card_h,
                            header_below=l_top + n_l * slot + 6)
        s.box(40, divider, end - 40, 9, bg=s.pill_bg, radius='4px', pad='0')
        if gf:
            # The grand final grows to 664px, centred in the room between the rounds and the side column.
            room_l, room_r = end + 80, RIGHT_X - 40
            gw = min(664, room_r - room_l)
            gx = room_l + (room_r - room_l - gw) / 2
            s.pill(gx, y0 - 18, gw, 'Grand final', 48, 24)   # bigger than the round headers, same bottom edge
            ends = [c[r_m['id']] for c, cols in ((w_centers, w_cols), (l_centers, l_cols)) if cols
                    for r_m in by_round[cols[-1]][:1]]
            gy = sum(ends) / len(ends) if ends else 500
            # Its robot rows grow to 135px, about twice a round's, keeping clear of the header above and
            # the bottom of the screen.
            top_y, bottom_y = y0 + 40, BOTTOM - 10
            row = min(135, (bottom_y - top_y - 30 - 38) / 2)
            gh = 30 + 2 * row
            gy = min(max(gy, top_y + gh / 2), bottom_y - 38 - gh / 2)
            s.card(gf, gx, gw, gy, gh, underway(gf))
            if gf['state'] != 'complete':
                s.box(gx, gy + gh / 2 + 8, gw, 30, 'May need a reset match', fs=13, justify='center', **TBD)
    else:
        n = max((len(by_round[r]) for r in w_cols), default=1) + (1 if third else 0)
        slot = min(240, (room - 56) / n)
        card_h = min(170, slot - 14)
        y0 = 24 + (room - 56 - n * slot) / 2
        # Few rounds left: the columns stop at 520px wide and sit in the middle of the space.
        n = max(len(w_cols), 1)
        w = min(520, (1280 - (n - 1) * 100) / n)
        end = 680 + (n * w + (n - 1) * 100) / 2
        centers = columns(w_cols, end, w, 100, w_name, y0, y0 + 56, slot, card_h)
        if third:
            final = by_round[w_cols[-1]][0]
            x = s.pos[final['id']][0]
            s.card(third, x, s.pos[final['id']][2], centers[final['id']] + card_h + 60, card_h, underway(third))
            s.box(x, centers[final['id']] + card_h / 2 + 12, s.pos[final['id']][2], 26, '3rd place match',
                  fs=13, justify='center', **TBD)

    for m in ms:
        if m is not reset:   # the reset sits right under the grand final; no ribbon doubling back
            s.ribbons_into(m)

    # --- the side column ---------------------------------------------------------------------------
    x, w = RIGHT_X, RIGHT_W
    y = 24
    s.box(x, y, w, 64, 'Still standing', str(len(alive)), bg='#121212', fs=24, xfs='1.9em')
    y += 76

    def section(title):
        nonlocal y
        s.pill(x, y, w, title)
        y += 44

    if double:
        unbeaten = [p for p in alive if not losses[p]]
        one_life = [p for p in alive if losses[p]]
        if unbeaten:
            section('Unbeaten')
            for p in unbeaten:
                s.box(x, y, w, 36, s.name(p), f'seed {s.seeds.get(p)}', bg=color_of(s.seeds.get(p)), fs=17)
                y += 40
            y += 12
        if one_life:
            section('One life left')
            for p in one_life:
                s.box(x, y, w, 36, s.name(p), f"lost to {s.name(losses[p][0]['winner_id'])}",
                      bg=color_of(s.seeds.get(p)), fs=17)
                y += 40
            y += 12

    # The podium comes in once it's down to the last 3: decided places by name, the rest by where
    # they'll come from.
    if len(alive) <= 3:
        final_match = reset if reset and reset['state'] == 'complete' else gf if double else (
            by_round[w_rounds[-1]][0] if w_rounds else None)
        decided = final_match and final_match['state'] == 'complete' and (
            not double or final_match is reset or final_match.get('winner_id') == final_match.get('player1_id'))
        # While a reset match is on, it decides 1st and 2nd.
        decider = 'grand final reset' if reset_on else 'grand final'
        places = [
            ('1st', s.name(final_match['winner_id']) if decided else None,
             final_match['winner_id'] if decided else None, f'Winner of the {decider}' if double else 'Winner of the final'),
            ('2nd', s.name(final_match['loser_id']) if decided else None,
             final_match['loser_id'] if decided else None, f'Loser of the {decider}' if double else 'Loser of the final'),
        ]
        if double:
            lf = by_round[l_rounds[-1]][0] if l_rounds else None
            lost = lf and lf['state'] == 'complete'
            places.append(('3rd', s.name(lf['loser_id']) if lost else None, lf['loser_id'] if lost else None,
                           'Loser of the second chance final'))
        elif third:
            lost = third['state'] == 'complete'
            places.append(('3rd', s.name(third['winner_id']) if lost else None, third['winner_id'] if lost else None,
                           'Winner of the 3rd place match'))
        else:
            # No 3rd place match: both semifinal losers share 3rd.
            semis = by_round[w_rounds[-2]] if len(w_rounds) > 1 else []
            losers = [m['loser_id'] for m in semis if m['state'] == 'complete']
            still = [m for m in semis if m['state'] != 'complete']
            places.append(('3rd', ' / '.join(s.name(p) for p in losers) or None, losers[0] if len(losers) == 1 else None,
                           'Both semifinal losers', ' '.join(f"+ loser of {s.label(m)}" for m in still) if losers else ''))
        section('Podium')
        for place, text, pid, pending, *more in places:
            s.box(x, y, 64, 36, place, bg='#121212', fs=16, justify='center', radius='6px 0 0 6px')
            if text:
                s.box(x + 64, y, w - 64, 36, text, more[0] if more else '',
                      bg=color_of(s.seeds.get(pid)) if pid else '#4f4f4f', fs=17, xfs='0.8em', radius='0 6px 6px 0')
            else:
                s.box(x + 64, y, w - 64, 36, pending, fs=16, radius='0 6px 6px 0', **TBD)
            y += 40
        y += 12

    return {'boxes': s.boxes, 'ribbons': s.ribbons, 'robots_left': len(alive)}


PLACE_COLOURS = {'1st': '#FFD700', '2nd': '#C0C0C0', '3rd': '#CD7F32'}


def final_results(matches, participants, tournament_type, places, pill_bg='#2C2C2C', note=None):
    """The screen once the tournament is decided: the champion large, 2nd and 3rd beside each other,
    and the champion's matches in the order they were played. places is the (1st, 2nd, 3rd) names
    the bracket page already works out; 3rd may be two names joined by ' / ' when they share it."""
    s = _Screen([m for m in matches if m.get('group_id') is None] or matches, participants, False, pill_bg)
    by_name = {p['name']: p['id'] for p in participants}
    done = [m for m in matches if m['state'] == 'complete' and m.get('winner_id')]

    def record(pid):
        wins = sum(1 for m in done if m['winner_id'] == pid)
        losses = sum(1 for m in done if m.get('loser_id') == pid)
        return f'{wins}–{losses}'

    def place_row(x, y, w, h, place, name, block_w, place_fs, name_fs, with_seed):
        s.box(x, y, block_w, h, place, bg='#121212', fg=PLACE_COLOURS[place], fs=place_fs, justify='center',
              radius='6px 0 0 6px')
        pid = by_name.get(name)
        if not name:
            s.box(x + block_w, y, w - block_w, h, 'TBD', fs=name_fs, radius='0 6px 6px 0', **TBD)
            return
        extra = (f"seed {s.seeds.get(pid)} · {record(pid)}" if with_seed else record(pid)) if pid else ''
        xscale = 0.42 if with_seed else 0.6
        # Name and its seed/record share the row: shrink the name until both fit (about 0.56em a character).
        name_fs = max(16, min(name_fs, int((w - block_w - 76) / (0.56 * (len(name) + len(extra) * xscale)))))
        s.box(x + block_w, y, w - block_w, h, name, extra, bg=color_of(s.seeds.get(pid)) if pid else '#4f4f4f',
              fs=name_fs, xfs=f'{xscale}em', pad='0 28px', radius='0 6px 6px 0')

    first, second, third = (list(places) + [None] * 3)[:3]
    champ = by_name.get(first)
    # The champion's matches as the /live boards' "How they got here" draws them (same data, story.py).
    path = story.Bracket(matches, participants, tournament_type).path(champ) if champ else []
    top_h = 72 + (46 if note else 0) + 210 + 150
    height = top_h + (48 + 116 if path else -30)
    x, w = 360, 1200
    y = max(30, (BOTTOM - height) / 2 - 30)   # the whole block centred above the tournament name, nudged up
    s.pill(x, y, w, 'Final results', 56, 30)
    y += 72
    if note:
        s.box(x, y, w, 34, note, fs=17, justify='center', **TBD)
        y += 46
    place_row(x, y, w, 190, '1st', first, 200, 64, 76, True)
    y += 210
    half = (w - 20) / 2
    place_row(x, y, half, 120, '2nd', second, 130, 40, 44, False)
    place_row(x + half + 20, y, half, 120, '3rd', third, 130, 40, 44, False)
    y += 150
    if path:
        # Full width, as on the /live boards, so each fight has room for both names.
        s.pill(40, y, 1840, "Champion's path", 32, 16)
        _path_row(s, path, s.seeds.get(champ), s.name(champ), y + 48, 40, 1880)
    return {'boxes': s.boxes, 'ribbons': s.ribbons}


def _path_row(s, path, seed, name, top, left, right):
    """One robot's fights left to right, as on the /live boards: its own box (round and W/L) with the
    opponent under it, dropping a level for a loss or a second chance round, later fights a little
    wider, joined by ribbons in its colour."""
    n = len(path)
    gap = 40 if n <= 4 else 28
    wt = [1 + 0.5 * c / (n - 1) if n > 1 else 1 for c in range(n)]
    unit = (right - left - (n - 1) * gap) / sum(wt)
    x, last = left, None
    seen_gf = False
    for c, e in enumerate(path):
        tag = e['r']
        if tag == 'GF':   # the reset is a second grand final match
            tag, seen_gf = ('GF 2' if seen_gf else 'GF'), True
        cw = unit * wt[c]
        small = cw < 200
        pad = '0 8px' if small else '0 14px'
        low = e['lost'] or e['r'].startswith('LR')
        ry, oy = (top + 32, top) if low else (top, top + 40)
        # Wins are the norm on a champion's path, so only a loss is marked. A name too long for the box
        # is cut short; the colour says whose path it is.
        s.box(x, ry, cw, 40, name, f'{tag} · L' if e['lost'] else tag, bg=color_of(seed), fs=15 if small else 18,
              xfs='0.75em', pad=pad, radius='0 0 6px 6px' if low else '6px 6px 0 0')
        s.box(x, oy, cw, 32, e['opp']['name'], '' if small else f"seed {e['opp']['seed']}",
              bg=color_of(e['opp']['seed']), fs=13 if small else 15, fw=600, pad=pad,
              radius='6px 6px 0 0' if low else '0 0 6px 6px')
        if last:
            s.ribbons.append(_band(last[0], last[1], 40, x, ry, 40, color_of(seed)))
        last = (x + cw, ry)
        x += cw + gap
