"""Build data for the always-relevant story screens (overview, upset watch, elimination match).

Everything here works on raw Challonge match and participant dicts so it can be
tested without the API. The browser renders the returned dict in static/js/story_screen.js.
"""
from datetime import datetime, timezone

SCREEN_MODES = ('overview', 'upset', 'elimination')
GRAND_FINAL_STAGE = 1000


def _parse_time(value):
    """Return an aware datetime for a Challonge timestamp (string or datetime), or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ordinal(n):
    """Return 1st, 2nd, 3rd, 4th, 11th, 21st..."""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


class Bracket:
    """Indexes one tournament's matches so story questions are cheap to answer."""

    def __init__(self, matches, participants, tournament_type='double elimination', now=None):
        self.now = now or datetime.now(timezone.utc)
        self.double = 'double' in (tournament_type or '').lower()
        self.players = {}
        for p in participants:
            info = {'id': p['id'], 'name': p.get('name') or 'Unknown', 'seed': p.get('seed') or 0}
            self.players[p['id']] = info
            for gpid in (p.get('group_player_ids') or []):
                self.players[gpid] = info
        self.matches = [m for m in matches if m.get('group_id') is None]
        self.by_id = {m['id']: m for m in self.matches}
        self.winners_max = max((m['round'] for m in self.matches if m['round'] > 0 and not self.is_grand_final(m)), default=0)
        self.losers_max = max((-m['round'] for m in self.matches if m['round'] < 0), default=0)

    # --- structure -------------------------------------------------------------------------

    def is_grand_final(self, m):
        """A winners-side match fed by a losers-bracket match (or the reset after it)."""
        if not self.double or m['round'] <= 0:
            return False
        for key in ('player1_prereq_match_id', 'player2_prereq_match_id'):
            prereq = self.by_id.get(m.get(key))
            if prereq and (prereq['round'] < 0 or self.is_grand_final(prereq)):
                return True
        return False

    def is_reset(self, m):
        p1, p2 = m.get('player1_prereq_match_id'), m.get('player2_prereq_match_id')
        return p1 is not None and p1 == p2

    def next_match(self, m, for_loser):
        """Return (match, slot) the winner or loser of m moves to, or (None, None)."""
        for other in self.matches:
            for slot in (1, 2):
                if other.get(f'player{slot}_prereq_match_id') != m['id']:
                    continue
                if bool(other.get(f'player{slot}_is_prereq_match_loser')) == for_loser:
                    return other, slot
        return None, None

    def eliminates(self, m):
        nxt, _ = self.next_match(m, for_loser=True)
        return nxt is None

    def stage(self, m):
        if self.is_grand_final(m):
            return GRAND_FINAL_STAGE
        return abs(m['round'])

    def finish_if_out(self, m):
        """Placing for a robot eliminated in match m: the champion plus one robot per later eliminating match."""
        eliminating = [e for e in self.matches
                       if (self.eliminates(e) or self.is_grand_final(e)) and not self.is_reset(e)]
        if self.is_grand_final(m):
            return 2
        if self.double and m['round'] > 0:
            return None
        return 2 + sum(1 for e in eliminating if self.stage(e) > self.stage(m))

    def guaranteed_finish(self, m):
        """Worst placing for a robot in match m (losing now and, if alive, the next one)."""
        if self.eliminates(m):
            return self.finish_if_out(m)
        nxt, _ = self.next_match(m, for_loser=True)
        while nxt is not None and not self.eliminates(nxt):
            nxt, _ = self.next_match(nxt, for_loser=True)
        return self.finish_if_out(nxt) if nxt else None

    # --- labels ----------------------------------------------------------------------------

    def round_short(self, m):
        if self.is_grand_final(m):
            return 'GF'
        if m['round'] < 0:
            return f'LR{-m["round"]}'
        return f'WR{m["round"]}' if self.double else f'R{m["round"]}'

    def round_name(self, m):
        if self.is_grand_final(m):
            return 'Grand Final'
        if m['round'] < 0:
            return 'Losers Final' if -m['round'] == self.losers_max else f'Losers Round {-m["round"]}'
        if m['round'] == self.winners_max:
            return 'Winners Final' if self.double else 'Final'
        return f'Winners Round {m["round"]}' if self.double else f'Round {m["round"]}'

    def player(self, pid):
        return self.players.get(pid, {'id': pid, 'name': 'Unknown', 'seed': 0})

    def chip(self, pid):
        p = self.player(pid)
        return {'seed': p['seed'], 'name': p['name']}

    def slot_text(self, m, slot):
        """Name of who will fill a slot: the robot, 'A or B', or 'TBD'."""
        pid = m.get(f'player{slot}_id')
        if pid:
            return self.player(pid)['name']
        prereq = self.by_id.get(m.get(f'player{slot}_prereq_match_id'))
        if prereq and prereq.get('player1_id') and prereq.get('player2_id'):
            return f'{self.player(prereq["player1_id"])["name"]} or {self.player(prereq["player2_id"])["name"]}'
        return 'TBD'

    def opponent_text(self, m, for_loser):
        """Who the winner (or loser) of m meets next."""
        nxt, slot = self.next_match(m, for_loser)
        if nxt is None:
            return None, None
        return nxt, self.slot_text(nxt, 2 if slot == 1 else 1)

    # --- player history --------------------------------------------------------------------

    def completed(self):
        done = [m for m in self.matches if m.get('state') == 'complete' and m.get('winner_id') and m.get('loser_id')]
        return sorted(done, key=lambda m: (_parse_time(m.get('completed_at')) or datetime.min.replace(tzinfo=timezone.utc), m['id']))

    def history(self, pid):
        return [m for m in self.completed() if pid in (m.get('player1_id'), m.get('player2_id'))]

    def path(self, pid):
        entries = []
        for m in self.history(pid):
            opp = m['player2_id'] if m['player1_id'] == pid else m['player1_id']
            entries.append({'r': self.round_short(m), 'opp': self.chip(opp), 'lost': m['loser_id'] == pid})
        return entries

    def record(self, pid):
        hist = self.history(pid)
        wins = sum(1 for m in hist if m['winner_id'] == pid)
        return wins, len(hist) - wins

    def beaten(self, pid):
        return [self.player(m['loser_id']) for m in self.history(pid) if m['winner_id'] == pid]

    def upset_wins(self, pid):
        own = self.player(pid)['seed'] or 0
        return sum(1 for p in self.beaten(pid) if p['seed'] and own and p['seed'] < own)

    def minutes_since_last_fight(self, pid):
        hist = self.history(pid)
        if not hist:
            return None
        last = _parse_time(hist[-1].get('completed_at'))
        if not last:
            return None
        return max(0, int((self.now - last).total_seconds() // 60))

    # --- what's on ---------------------------------------------------------------------------

    def ready(self):
        """Matches with both robots known and no result yet, in play order."""
        live = [m for m in self.matches if m.get('state') == 'open' and m.get('player1_id') and m.get('player2_id')]
        return sorted(live, key=lambda m: (m.get('suggested_play_order') or 10 ** 6, m['id']))


def _last_fight_text(minutes):
    if minutes is None:
        return 'First fight'
    if minutes < 60:
        return f'{minutes} min ago'
    if minutes < 6 * 60:
        return f'{minutes // 60} h {minutes % 60} min ago'
    return 'Earlier'


def _vs_seed_text(seed, finish):
    if not seed or not finish:
        return 'On track'
    ahead = seed - finish
    if ahead <= 0:
        return 'On track'
    return f'{ahead} spot{"s" if ahead != 1 else ""} ahead'


def _seeds_text(players):
    seeds = [str(p['seed']) for p in players if p['seed']]
    return ' · '.join(seeds) if seeds else 'None yet'


def build_finished(b, limit):
    out = []
    for m in reversed(b.completed()[-limit:]):
        winner, loser = b.player(m['winner_id']), b.player(m['loser_id'])
        nxt, _ = b.next_match(m, for_loser=True)
        if nxt is None:
            where = 'eliminated'
        elif b.is_grand_final(nxt):
            where = 'goes to a Grand Final reset'
        else:
            where = f'drops to {b.round_name(nxt).replace("Round ", "R")}'
        out.append({'seed': winner['seed'], 'name': winner['name'], 'round': b.round_short(m),
                    'detail': f'def. {loser["name"]} · {where}'})
    return out


def build_coming_up(b, limit, skip_id=None, with_round=False):
    out = []
    for m in b.ready():
        if m['id'] == skip_id:
            continue
        order = m.get('suggested_play_order')
        tag = f'#{order}' if order else b.round_short(m)
        if with_round and order:
            tag = f'{b.round_short(m)} · #{order}'
        nxt, opp = b.opponent_text(m, for_loser=False)
        if nxt is None:
            faces = 'Winner takes the title'
        elif b.is_grand_final(nxt):
            faces = f'Winner faces {opp} in the Grand Final'
        else:
            faces = f'Winner faces {opp}'
        if b.eliminates(m) and nxt is not None:
            faces += ' · loser goes home'
        out.append({'tag': tag, 'a': b.chip(m['player1_id']), 'b': b.chip(m['player2_id']), 'faces': faces})
        if len(out) == limit:
            break
    return out


def pick_upset(b):
    """Ready match featuring the robot most out-performing its seed; else the hottest record."""
    best, best_key = None, None
    for m in b.ready():
        robots = (m['player1_id'], m['player2_id'])
        upsets = max(b.upset_wins(pid) for pid in robots)
        overachieve = max(sum(max(0, (b.player(pid)['seed'] or 0) - (p['seed'] or 0)) for p in b.beaten(pid)) for pid in robots)
        wins = sum(b.record(pid)[0] for pid in robots)
        key = (upsets, overachieve, wins)
        if best_key is None or key > best_key:
            best, best_key = m, key
    return best


def pick_elimination(b):
    for m in b.ready():
        if b.eliminates(m) and not b.is_reset(m):
            return m
    return None


def _robot_story(b, m, pid, mode):
    p = b.player(pid)
    wins, losses = b.record(pid)
    finish = b.guaranteed_finish(m)
    beaten = b.beaten(pid)
    if mode == 'upset':
        stats = [
            ['Record', f'{wins}–{losses}'],
            ['Seeds beaten', _seeds_text(beaten)],
            ['Upset wins', str(b.upset_wins(pid))],
            ['Vs. their seed', _vs_seed_text(p['seed'], finish)],
            ['Guaranteed finish', f'Top {finish}' if finish else '—'],
            ['Last fight', _last_fight_text(b.minutes_since_last_fight(pid))],
        ]
    else:
        losers_wins = sum(1 for h in b.history(pid) if h['round'] < 0 and h['winner_id'] == pid)
        stats = [
            ['Record', f'{wins}–{losses}'],
            ['Losses', f'{losses} of {2 if b.double else 1}'],
            ['Seeds beaten', _seeds_text(beaten)],
            ['Losers bracket wins', str(losers_wins)],
            ['Vs. their seed', _vs_seed_text(p['seed'], finish)],
            ['Last fight', _last_fight_text(b.minutes_since_last_fight(pid))],
        ]
    return {'seed': p['seed'], 'name': p['name'], 'path': b.path(pid), 'stats': stats}


def build_story(b, m, mode):
    win_next, win_opp = b.opponent_text(m, for_loser=False)
    if win_next is None:
        win = ['WINNER', 'Champion', 'takes the title']
    else:
        win = ['WINNER GOES TO', b.round_name(win_next), f'vs {win_opp}']
    lose_next, lose_opp = b.opponent_text(m, for_loser=True)
    if lose_next is not None and b.is_reset(lose_next):
        lose = ['LOSER GOES TO', 'Grand Final reset', 'rematch for the title']
    elif lose_next is None:
        place = b.finish_if_out(m)
        lose = ['LOSER GOES TO', 'Eliminated', f'finishes {_ordinal(place)}' if place else 'out of the event']
    else:
        lose = ['LOSER GOES TO', b.round_name(lose_next), f'vs {lose_opp}']
    return {
        'tag': 'UPSET WATCH' if mode == 'upset' else 'ELIMINATION MATCH',
        'round_name': b.round_name(m),
        'a': _robot_story(b, m, m['player1_id'], mode),
        'b': _robot_story(b, m, m['player2_id'], mode),
        'win': win,
        'lose': lose,
    }


def build_screen_data(tournament_name, matches, participants, tournament_type, mode,
                      pinned_match_id=None, now=None, background='#00ff00'):
    """Return everything the story screen needs. Falls back to overview if no match fits."""
    b = Bracket(matches, participants, tournament_type, now)
    data = {'tournament_name': tournament_name, 'background': background, 'mode': 'overview', 'story': None}
    featured = None
    if mode in ('upset', 'elimination'):
        pinned = b.by_id.get(pinned_match_id) if pinned_match_id else None
        if pinned and pinned in b.ready():
            featured = pinned
        else:
            featured = pick_upset(b) if mode == 'upset' else pick_elimination(b)
    if featured:
        data['mode'] = mode
        data['story'] = build_story(b, featured, mode)
        data['finished'] = build_finished(b, 3)
        data['coming_up'] = build_coming_up(b, 3, skip_id=featured['id'])
    else:
        data['finished'] = build_finished(b, 6)
        data['coming_up'] = build_coming_up(b, 6, with_round=True)
    return data


def ready_match_options(matches, participants, tournament_type):
    """Ready matches for the admin pin dropdown, plus which ones the app would pick."""
    b = Bracket(matches, participants, tournament_type)
    upset, elim = pick_upset(b), pick_elimination(b)
    return {
        'matches': [{'id': m['id'], 'label': f'{b.round_short(m)} · {b.player(m["player1_id"])["name"]} vs {b.player(m["player2_id"])["name"]}'}
                    for m in b.ready()],
        'auto_upset': upset['id'] if upset else None,
        'auto_elimination': elim['id'] if elim else None,
    }
