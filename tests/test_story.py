"""Tests for the story screen data builder (story.py) on a 4-robot double elimination bracket.

Bracket shape (Challonge style):
  m1 WR1  A(1) vs D(4)        m2 WR1  B(2) vs C(3)
  m3 WR2  W m1 vs W m2        m4 LR1  L m1 vs L m2
  m5 LR2  L m3 vs W m4        m6 GF   W m3 vs W m5      m7 reset  W m6 vs L m6
"""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from story import Bracket, build_screen_data, describe_match, match_choices, pick_elimination, pick_upset

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
A, B, C, D = 11, 12, 13, 14
PARTICIPANTS = [
    {'id': A, 'name': 'Alpha', 'seed': 1},
    {'id': B, 'name': 'Bravo', 'seed': 2},
    {'id': C, 'name': 'Charlie', 'seed': 3},
    {'id': D, 'name': 'Delta', 'seed': 4},
]


def match(mid, rnd, p1=None, p2=None, pre1=None, pre2=None, loser1=False, loser2=False,
          winner=None, completed=None, order=None):
    state = 'complete' if winner else ('open' if p1 and p2 else 'pending')
    loser = None
    if winner:
        loser = p2 if winner == p1 else p1
    return {
        'id': mid, 'round': rnd, 'state': state, 'player1_id': p1, 'player2_id': p2,
        'winner_id': winner, 'loser_id': loser,
        'player1_prereq_match_id': pre1, 'player2_prereq_match_id': pre2,
        'player1_is_prereq_match_loser': loser1, 'player2_is_prereq_match_loser': loser2,
        'completed_at': completed, 'suggested_play_order': order, 'group_id': None,
    }


def bracket_after_round_one():
    """Alpha beat Delta; Charlie (seed 3) upset Bravo (seed 2). WR2 and LR1 are ready."""
    return [
        match(1, 1, A, D, winner=A, completed='2026-10-18T11:30:00Z', order=1),
        match(2, 1, B, C, winner=C, completed='2026-10-18T11:45:00Z', order=2),
        match(3, 2, A, C, pre1=1, pre2=2, order=3),
        match(4, -1, D, B, pre1=1, pre2=2, loser1=True, loser2=True, order=4),
        match(5, -2, None, None, pre1=3, pre2=4, loser1=True, order=5),
        match(6, 3, None, None, pre1=3, pre2=5, order=6),
        match(7, 3, None, None, pre1=6, pre2=6, loser2=True, order=7),
    ]


def test_round_names_and_grand_final():
    b = Bracket(bracket_after_round_one(), PARTICIPANTS, 'double elimination', NOW)
    names = {m['id']: b.round_name(m) for m in b.matches}
    assert names[1] == 'Winners Round 1'
    assert names[3] == 'Winners Final'
    assert names[4] == 'Losers Round 1'
    assert names[5] == 'Losers Final'
    assert names[6] == 'Grand Final'
    assert b.is_reset(b.by_id[7])


def test_finishing_places():
    b = Bracket(bracket_after_round_one(), PARTICIPANTS, 'double elimination', NOW)
    assert b.finish_if_out(b.by_id[4]) == 4
    assert b.finish_if_out(b.by_id[5]) == 3
    assert b.finish_if_out(b.by_id[6]) == 2
    assert b.guaranteed_finish(b.by_id[3]) == 3


def test_picks_upset_and_elimination():
    b = Bracket(bracket_after_round_one(), PARTICIPANTS, 'double elimination', NOW)
    assert pick_upset(b)['id'] == 3
    assert pick_elimination(b)['id'] == 4


def test_path_puts_losses_and_records():
    b = Bracket(bracket_after_round_one(), PARTICIPANTS, 'double elimination', NOW)
    assert b.path(C) == [{'r': 'WR1', 'opp': {'seed': 2, 'name': 'Bravo'}, 'lost': False}]
    assert b.path(B) == [{'r': 'WR1', 'opp': {'seed': 3, 'name': 'Charlie'}, 'lost': True}]
    assert b.record(C) == (1, 0)
    assert b.upset_wins(C) == 1
    assert b.minutes_since_last_fight(C) == 15


def test_upset_screen_data():
    data = build_screen_data('Test Cup', bracket_after_round_one(), PARTICIPANTS, 'double elimination', 'upset', now=NOW)
    story = data['story']
    assert data['mode'] == 'upset'
    assert story['tag'] == 'UPSET WATCH'
    assert story['round_name'] == 'Winners Final'
    assert story['win'] == ['WINNER GOES TO', 'Grand Final', 'vs TBD']
    assert story['lose'] == ['LOSER GOES TO', 'Losers Final', 'vs Delta or Bravo']
    assert dict(story['b']['stats'])['Upset wins'] == '1'
    assert [c['tag'] for c in data['coming_up']] == ['#4']
    assert data['finished'][0]['detail'] == 'def. Bravo · drops to Losers R1'


def test_elimination_screen_data():
    data = build_screen_data('Test Cup', bracket_after_round_one(), PARTICIPANTS, 'double elimination', 'elimination', now=NOW)
    story = data['story']
    assert story['round_name'] == 'Losers Round 1'
    assert story['lose'] == ['LOSER GOES TO', 'Eliminated', 'finishes 4th']
    assert story['win'] == ['WINNER GOES TO', 'Losers Final', 'vs Alpha or Charlie']
    assert dict(story['a']['stats'])['Losses'] == '1 of 2'


def test_winner_faces_names_both_possible_opponents():
    matches = bracket_after_round_one()
    matches[0] = match(1, 1, A, D, order=1)
    matches[1] = match(2, 1, B, C, order=2)
    matches[2] = match(3, 2, None, None, pre1=1, pre2=2, order=3)
    data = build_screen_data('Test Cup', matches, PARTICIPANTS, 'double elimination', 'overview', now=NOW)
    assert data['coming_up'][0]['faces'] == 'Winner faces Bravo or Charlie'
    assert data['coming_up'][0]['tag'] == 'WR1 · #1'


def test_pinned_match_wins_over_auto_and_falls_back_to_overview():
    data = build_screen_data('Test Cup', bracket_after_round_one(), PARTICIPANTS, 'double elimination', 'upset',
                             pinned_match_id=4, now=NOW)
    assert data['story']['round_name'] == 'Losers Round 1'

    done = bracket_after_round_one()
    done[2] = match(3, 2, A, C, pre1=1, pre2=2, winner=A, completed='2026-10-18T11:50:00Z', order=3)
    done[3] = match(4, -1, D, B, pre1=1, pre2=2, loser1=True, loser2=True, winner=B, completed='2026-10-18T11:55:00Z', order=4)
    done[4] = match(5, -2, C, B, pre1=3, pre2=4, loser1=True, order=5)
    elim = build_screen_data('Test Cup', done, PARTICIPANTS, 'double elimination', 'elimination', now=NOW)
    assert elim['story']['round_name'] == 'Losers Final'
    assert elim['story']['lose'][2] == 'finishes 3rd'
    low = [e['lost'] for e in elim['story']['b']['path']]
    assert low == [True, False]


def test_no_ready_story_match_falls_back_to_overview():
    matches = [match(1, 1, A, D, order=1)]
    data = build_screen_data('Test Cup', matches, PARTICIPANTS[:1] + PARTICIPANTS[3:], 'single elimination', 'upset', now=NOW)
    assert data['mode'] == 'upset'
    single = build_screen_data('Test Cup', [], PARTICIPANTS, 'single elimination', 'elimination', now=NOW)
    assert single['mode'] == 'overview'
    assert single['story'] is None


def test_match_choices_labels_and_order():
    choices = match_choices(bracket_after_round_one(), PARTICIPANTS, 'double elimination')
    assert choices['upset'] == [{'id': 3, 'label': 'Alpha/Charlie 1/3 W2'}, {'id': 4, 'label': 'Delta/Bravo 4/2 L1'}]
    assert choices['elimination'] == [{'id': 4, 'label': 'Delta/Bravo 4/2 L1'}]


def test_match_choices_limit_keeps_pinned_match():
    choices = match_choices(bracket_after_round_one(), PARTICIPANTS, 'double elimination', limit=1)
    assert [c['id'] for c in choices['upset']] == [3]
    pinned = match_choices(bracket_after_round_one(), PARTICIPANTS, 'double elimination',
                           pins={'upset': 4}, limit=1)
    assert [c['id'] for c in pinned['upset']] == [3, 4]


def test_describe_match():
    assert describe_match(bracket_after_round_one(), PARTICIPANTS, 'double elimination', 3) == 'Alpha/Charlie 1/3 W2'
    assert describe_match(bracket_after_round_one(), PARTICIPANTS, 'double elimination', 5) is None


def test_strict_pin_falls_back_to_overview_not_auto():
    done = bracket_after_round_one()
    done[3] = match(4, -1, D, B, pre1=1, pre2=2, loser1=True, loser2=True, winner=B, completed='2026-10-18T11:55:00Z', order=4)
    data = build_screen_data('Test Cup', done, PARTICIPANTS, 'double elimination', 'upset',
                             pinned_match_id=4, now=NOW, strict=True)
    assert data['mode'] == 'overview'
    loose = build_screen_data('Test Cup', done, PARTICIPANTS, 'double elimination', 'upset',
                              pinned_match_id=4, now=NOW)
    assert loose['mode'] == 'upset'


def test_upcoming_lists_ready_fights_with_where_the_winner_goes():
    from story import build_upcoming
    rows = build_upcoming([('Beetles', bracket_after_round_one(), PARTICIPANTS, 'double elimination')])
    assert [(r['a']['name'], r['b']['name']) for r in rows] == [('Alpha', 'Charlie'), ('Delta', 'Bravo')]
    assert rows[0]['round'] == 'Winners Final'
    assert rows[1]['round'] == 'Losers R1'
    assert rows[1]['faces'].endswith('elimination match')
    assert not any(r['underway'] for r in rows)


def test_upcoming_puts_underway_fights_first_and_interleaves_tournaments():
    from story import build_upcoming
    beetles = bracket_after_round_one()
    beetles[3] = dict(beetles[3], underway_at='2026-10-18T11:58:00Z')
    ants = [dict(m, id=m['id'] + 100) for m in bracket_after_round_one()]
    for m in ants:
        for key in ('player1_prereq_match_id', 'player2_prereq_match_id'):
            if m[key]:
                m[key] += 100
    rows = build_upcoming([('Beetles', beetles, PARTICIPANTS, 'double elimination'),
                           ('Ants', ants, PARTICIPANTS, 'double elimination')])
    assert [(r['tournament'], r['round'], r['underway']) for r in rows] == [
        ('Beetles', 'Losers R1', True),
        ('Beetles', 'Winners Final', False),
        ('Ants', 'Winners Final', False),
        ('Ants', 'Losers R1', False),
    ]
