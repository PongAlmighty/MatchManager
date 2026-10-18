"""Tests for the "Road to the final" end screen (endgame.py).

The fixtures are real Challonge snapshots of two test tournaments: a 36-robot double elimination with
6 robots left, and a 10-robot single elimination with 3 left.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import endgame  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def screen(name):
    d = load(name)
    return endgame.build(d['matches'], d['participants'], d['tournament']['tournament_type'])


def rect(box):
    v = dict(re.findall(r'(left|top|width|height):([\d.]+)px', box['style']))
    return float(v['left']), float(v['top']), float(v['width']), float(v['height'])


def texts(s):
    return [b['text'] for b in s['boxes']]


def assert_fits_without_overlap(s):
    rects = [rect(b) for b in s['boxes']]
    for x, y, w, h in rects:
        assert x >= 0 and x + w <= 1920
        assert y >= 0 and y + h <= endgame.BOTTOM
    for i, (x, y, w, h) in enumerate(rects):
        for x2, y2, w2, h2 in rects[i + 1:]:
            overlap_x = min(x + w, x2 + w2) - max(x, x2)
            overlap_y = min(y + h, y2 + h2) - max(y, y2)
            assert overlap_x <= 0.5 or overlap_y <= 0.5, f'boxes overlap at ({x}, {y}) and ({x2}, {y2})'


def test_double_elim_with_6_left():
    s = screen('double_elim_6_left.json')
    assert s['robots_left'] == 6
    t = texts(s)
    for header in ('Winners semis', 'Winners final', 'Grand final',
                   'Second chance R7', 'Second chance R8', 'Second chance final',
                   'Unbeaten', 'One life left', 'May need a reset match'):
        assert header in t
    for gone in ('Winners R4', 'Second chance R6', 'Knocked out', 'Podium'):   # finished rounds; no out list, no podium until 3 left
        assert gone not in t
    assert 'Loser of AG' in t   # a winners semi loser drops into second chance, named in its slot
    assert_fits_without_overlap(s)


def test_single_elim_with_3_left():
    s = screen('single_elim_3_left.json')
    assert s['robots_left'] == 3
    t = texts(s)
    for header in ('Semifinals', 'Final', 'Podium'):
        assert header in t
    for gone in ('Quarterfinals', 'Unbeaten', 'Knocked out'):
        assert gone not in t
    third = next(b for b in s['boxes'] if b['text'] == 'Piston Pucksters' and 'loser of H' in b['extra'])
    assert third
    assert_fits_without_overlap(s)


def test_underway_match_turns_gold():
    d = load('single_elim_3_left.json')
    for m in d['matches']:
        if m['identifier'] == 'H':
            m['underway_at'] = '2026-10-17T12:00:00+00:00'
    s = endgame.build(d['matches'], d['participants'], d['tournament']['tournament_type'])
    gold = [b['text'] for b in s['boxes'] if 'background:gold' in b['style']]
    assert gold == ['Match H', 'Magnet Maulers', 'Sparkplug Slappers']


def test_only_for_elimination_with_8_or_fewer_left():
    d = load('single_elim_3_left.json')
    assert endgame.build(d['matches'], d['participants'], 'round robin') is None
    fresh = [dict(m, state='open' if m['round'] == 1 else 'pending', winner_id=None, loser_id=None)
             for m in d['matches']]
    assert endgame.build(fresh, d['participants'], 'single elimination') is None   # all 10 still in


def test_final_results_screen():
    d = load('double_elim_6_left.json')
    names = sorted(p['name'] for p in d['participants'])
    s = endgame.final_results(d['matches'], d['participants'], d['tournament']['tournament_type'], names[:3])
    t = texts(s)
    for label in ('Final results', '1st', '2nd', '3rd', "Champion's path"):
        assert label in t
    assert names[0] in t and names[1] in t and names[2] in t
    assert_fits_without_overlap(s)


def test_final_results_shared_third_and_pending_note():
    d = load('single_elim_3_left.json')
    names = [p['name'] for p in d['participants']]
    s = endgame.final_results(d['matches'], d['participants'], 'round robin',
                              (names[0], names[1], f'{names[2]} / {names[3]}'), note='Pending finalization')
    t = texts(s)
    assert 'Pending finalization' in t
    assert f'{names[2]} / {names[3]}' in t
    assert_fits_without_overlap(s)


def test_final_results_with_nobody_placed_yet():
    d = load('single_elim_3_left.json')
    s = endgame.final_results(d['matches'], d['participants'], 'single elimination', (None, None, None))
    assert texts(s).count('TBD') == 3
    assert "Champion's path" not in texts(s)


def test_double_elim_reset_match():
    # Turbine Terror won the winners final and lost the grand final to Circuit Breaker from second chance.
    s = screen('double_elim_reset.json')
    assert s['robots_left'] == 2
    t = texts(s)
    # The played grand final moves into the rounds column; the reset is the big match.
    assert 'Grand final reset' in t and 'Grand final' in t
    assert 'Winners final' not in t and 'Second chance final' not in t
    assert 'Winner of the grand final reset' in t and 'May need a reset match' not in t
    # Losing the grand final is Turbine Terror's first loss: not out.
    assert not any(b['text'] == 'Turbine Terror' and b['extra'] == 'out' for b in s['boxes'])
    # ...so that row keeps its colour under grey stripes: lost that match, still in it.
    assert any(b['text'] == 'Turbine Terror' and b['extra'] == 'L' and 'repeating-linear-gradient' in b['style']
               for b in s['boxes'])
    # One ribbon per robot, from the grand final into the reset.
    assert len(s['ribbons']) == 2
    assert_fits_without_overlap(s)


def test_no_knocked_out_list_and_podium_only_from_3_left():
    for name in ('double_elim_6_left.json', 'single_elim_3_left.json', 'double_elim_reset.json'):
        s = screen(name)
        t = texts(s)
        assert 'Knocked out' not in t
        assert ('Podium' in t) == (s['robots_left'] <= 3)
