"""Bracket display for OBS, ported from Brackinator.

Turns one tournament's raw Challonge data into the template data for templates/tournament.html:
double elimination (winners + losers), round robin / Swiss, group stage, the three-competitor
endgame and the podium. The code is kept close to Brackinator's so the display behaves the same.
"""
from functools import cmp_to_key

import endgame

UNSEEDED_PLAYER_COLOR = "#b5d16c"

# 42 visually distinct player colours, assigned by seed.
COLOR_PALETTE = [
    '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', '#46f0f0', '#f032e6',
    '#bcf60c', '#fabebe', '#008080', '#e6beff', '#9a6324', '#fffac8', '#800000', '#aaffc3',
    '#808000', '#ffd8b1', '#000075', '#808080', '#ffffff', '#000000', '#a9a9a9', '#b22222',
    '#228b22', '#ff69b4', '#8a2be2', '#00ced1', '#daa520', '#ff4500', '#2e8b57', '#20b2aa',
    '#ff6347', '#4682b4', '#d2691e', '#9acd32', '#5f9ea0', '#ffb6c1', '#9932cc', '#bdb76b',
    '#8b0000', '#00ff7f'
]


def create_participants_lookup(participants):
    """Create a lookup dictionary for participants by ID.

    Maps both main participant IDs and group_player_ids to participant info,
    so group stage matches (which reference players by group_player_id) resolve
    correctly instead of showing 'Unknown Player'.
    """
    lookup = {}
    for p in participants:
        info = {'name': p['name'], 'seed': p.get('seed', 0), 'final_rank': p.get('final_rank')}
        lookup[p['id']] = info
        for gpid in (p.get('group_player_ids') or []):
            lookup[gpid] = info
    return lookup

def determine_bracket_and_round(match):
    """Determine bracket type and round number from match data."""
    round_num = match.get('round', 0)
    if round_num < 0:
        bracket = 'losers'
        round_num = abs(round_num)
    else:
        bracket = 'winners'
        round_num = round_num if round_num > 0 else 1
    
    # Handle grand finals (round 0)
    if match.get('round') == 0:
        bracket = 'winners'
        round_num = 999
        
    return bracket, round_num

def process_player_details(player_id, participants_data, add_tbd_info=False, prereq_match_id=None, match_id=None):
    """Process player details with consistent handling of IDs and TBD status."""
    if player_id:
        # Real player with ID
        player_details = participants_data.get(player_id, {}).copy()
        player_details['id'] = player_id
        player_details.setdefault('name', 'Unknown Player')
        player_details.setdefault('seed', None)
        if add_tbd_info:
            player_details['is_tbd'] = False
    elif add_tbd_info and prereq_match_id:
        # TBD player fed by a previous match
        player_details = {
            'name': 'To Be Determined',
            'id': f'tbd_p{match_id}',
            'is_tbd': True,
            'seed': None,
            'fed_by_match_id': prereq_match_id
        }
    elif add_tbd_info:
        # BYE or open slot
        player_details = {
            'name': 'BYE / Open',
            'id': f'open_p{match_id}',
            'is_tbd': True,
            'seed': None
        }
    else:
        # Basic empty details
        player_details = {'name': ''}
        if player_id:
            player_details['id'] = player_id
    
    return player_details

def process_match_basic(match, participants_data):
    """Process match data with basic player information."""
    bracket, round_num = determine_bracket_and_round(match)
    player1_id = match.get('player1_id')
    player2_id = match.get('player2_id')
    
    player1_details = process_player_details(player1_id, participants_data)
    player2_details = process_player_details(player2_id, participants_data)
    
    return {
        'id': match['id'],
        'round': round_num,
        'bracket': bracket,
        'state': match.get('state', 'pending'),
        'player1': player1_details,
        'player2': player2_details,
        'winner_id': match.get('winner_id'),
        'loser_id': match.get('loser_id')
    }

def process_match_detailed(match, participants_data):
    """Process match data with detailed player information for display."""
    bracket, round_num = determine_bracket_and_round(match)
    player1_id = match.get('player1_id')
    player2_id = match.get('player2_id')
    player1_prereq_match_id = match.get('player1_prereq_match_id')
    player2_prereq_match_id = match.get('player2_prereq_match_id')
    
    player1_details = process_player_details(
        player1_id, participants_data, True, player1_prereq_match_id, f"1_match_{match['id']}"
    )
    player2_details = process_player_details(
        player2_id, participants_data, True, player2_prereq_match_id, f"2_match_{match['id']}"
    )
    
    processed_match = {
        'id': match['id'],
        'round': round_num,
        'bracket': bracket,
        'state': match.get('state', 'pending'),
        'player1': player1_details,
        'player2': player2_details,
        'winner_id': match.get('winner_id'),
        'loser_id': match.get('loser_id'),
        'underway_at': match.get('underway_at'),
        'player1_prereq_match_id': player1_prereq_match_id,
        'player2_prereq_match_id': player2_prereq_match_id,
        'has_outgoing_line': False,
        'feeds_into_match_id': None,
        'line_style': 'solid'
    }
    
    return processed_match

def assign_player_colors(participants, color_palette):
    """Assign colors to players based on seed."""
    player_colors = {}
    for p in participants:
        name = p['name']
        seed = p.get('seed', 0)
        if seed > 0:
            color = color_palette[(seed-1) % len(color_palette)]
        else:
            color = UNSEEDED_PLAYER_COLOR
        player_colors[name] = color
    return player_colors

def match_has_participants(match_obj):
    """Check if a match has actual participants based on player IDs."""
    p1_id = match_obj.get('player1', {}).get('id')
    p2_id = match_obj.get('player2', {}).get('id')
    return p1_id is not None or p2_id is not None

def get_potential_round_numbers(matches_for_bracket):
    """Get all potential round numbers for a bracket."""
    if not matches_for_bracket:
        return []
    max_round = max((m['round'] for m in matches_for_bracket), default=0)
    return list(range(1, max_round + 1)) if max_round > 0 else []

def filter_rounds_to_display(potential_rounds, bracket_matches):
    """Filter rounds for display based on active rounds."""
    if not potential_rounds or not bracket_matches:
        return []
    
    current_bracket_name = bracket_matches[0]['bracket'] if bracket_matches else 'UnknownBracket'
    
    # Find first active round (not complete and has participants)
    first_active_round_num = -1
    for r_num in sorted(potential_rounds):
        if any(m['round'] == r_num and m['state'] != 'complete' and match_has_participants(m) for m in bracket_matches):
            first_active_round_num = r_num
            break
    
    if first_active_round_num != -1:
        # We have at least one active round
        start_display_from_round = max(1, first_active_round_num - 1)
        
        # Find last active round
        last_active_round_num = -1
        for r_num in sorted(potential_rounds, reverse=True):
            if any(m['round'] == r_num and m['state'] != 'complete' and match_has_participants(m) for m in bracket_matches):
                last_active_round_num = r_num
                break
        
        if last_active_round_num == -1:
            last_active_round_num = first_active_round_num
        
        # Determine display range
        try:
            start_idx = potential_rounds.index(start_display_from_round) if start_display_from_round in potential_rounds else 0
            end_idx = potential_rounds.index(last_active_round_num) if last_active_round_num in potential_rounds else len(potential_rounds) - 1
            
            if end_idx < start_idx:
                end_idx = start_idx
                
            display_rounds = potential_rounds[start_idx:end_idx + 1]
            
            # Add one more empty round if available
            if end_idx + 1 < len(potential_rounds):
                display_rounds.append(potential_rounds[end_idx + 1])
                
            return display_rounds
        except (ValueError, IndexError):
            return potential_rounds[:1] if potential_rounds else []
    else:
        # No active rounds, show last round with participants and one after
        last_round_with_any_participants = -1
        for r_num in sorted(potential_rounds, reverse=True):
            if any(m['round'] == r_num and match_has_participants(m) for m in bracket_matches):
                last_round_with_any_participants = r_num
                break
        
        if last_round_with_any_participants != -1:
            display_rounds = [last_round_with_any_participants]
            try:
                current_idx = potential_rounds.index(last_round_with_any_participants)
                if current_idx + 1 < len(potential_rounds):
                    display_rounds.append(potential_rounds[current_idx + 1])
            except ValueError:
                pass
            
            return display_rounds
        else:
            # No rounds with participants, show first round
            return potential_rounds[:1] if potential_rounds else []

def determine_podium_finishers(processed_matches, participants_data):
    """Determine 1st, 2nd, and 3rd place finishers."""
    all_matches_resolved = all(m.get('state') == 'complete' for m in processed_matches if match_has_participants(m))
    
    first_place_name = None
    second_place_name = None
    third_place_name = None
    
    # Determine 3rd place from losers bracket
    all_loser_bracket_matches = [m for m in processed_matches if m['bracket'] == 'losers']
    if all_loser_bracket_matches and all(match.get('state') == 'complete' for match in all_loser_bracket_matches):
        losers_final_match = max(all_loser_bracket_matches, key=lambda m: m.get('round', 0))
        loser_id = losers_final_match.get('loser_id')
        if loser_id and loser_id in participants_data:
            third_place_name = participants_data[loser_id]['name']
    
    # Determine 1st and 2nd place if all matches are resolved
    if all_matches_resolved and processed_matches:
        # Get raw matches to access 'updated_at' for sorting
        completed_matches = [m for m in processed_matches if m.get('state') == 'complete' and m.get('winner_id') and m.get('loser_id')]
        if completed_matches:
            last_played_match = completed_matches[-1]
            winner_id = last_played_match.get('winner_id')
            loser_id = last_played_match.get('loser_id')
            if winner_id in participants_data:
                first_place_name = participants_data[winner_id]['name']
            if loser_id in participants_data:
                second_place_name = participants_data[loser_id]['name']
    
    return first_place_name, second_place_name, third_place_name

def determine_podium_from_final_ranks(participants, participants_data):
    id_to_name = {pid: info.get('name') for pid, info in participants_data.items()}
    ranked = [p for p in participants if p.get('final_rank')]
    ranked.sort(key=lambda p: p.get('final_rank'))
    names = []
    for p in ranked[:3]:
        names.append(id_to_name.get(p['id'], p.get('name')))
    while len(names) < 3:
        names.append(None)
    return tuple(names)

def compute_round_robin_swiss_standings(matches, participants, participants_data):
    stats = {}
    for p in participants:
        stats[p['id']] = {'wins': 0, 'losses': 0, 'ties': 0, 'set_wins': 0, 'set_losses': 0}
    
    # Track head-to-head results: (winner_id, loser_id) -> True
    who_beat_who = set()
    
    for m in matches:
        if m.get('state') != 'complete':
            continue
        p1 = m.get('player1_id')
        p2 = m.get('player2_id')
        if not p1 or not p2:
            continue
        winner = m.get('winner_id')
        loser = m.get('loser_id')
        if winner and loser:
            stats[winner]['wins'] += 1
            stats[loser]['losses'] += 1
            who_beat_who.add((winner, loser))
        else:
            stats[p1]['ties'] += 1
            stats[p2]['ties'] += 1
        scores_csv = m.get('scores_csv') or ''
        p1_sets = 0
        p2_sets = 0
        for part in scores_csv.split(','):
            part = part.strip()
            if '-' in part:
                try:
                    a, b = part.split('-', 1)
                    p1_sets += int(a.strip() or 0)
                    p2_sets += int(b.strip() or 0)
                except ValueError:
                    continue
        stats[p1]['set_wins'] += p1_sets
        stats[p1]['set_losses'] += p2_sets
        stats[p2]['set_wins'] += p2_sets
        stats[p2]['set_losses'] += p1_sets
    
    def compare_players(pid1, pid2):
        s1 = stats[pid1]
        s2 = stats[pid2]
        
        # 1. Match Wins (Higher is better)
        if s1['wins'] != s2['wins']:
            return s2['wins'] - s1['wins']
            
        # 2. Head-to-Head
        # If pid1 beat pid2, pid1 ranks higher (return negative)
        if (pid1, pid2) in who_beat_who:
            return -1
        # If pid2 beat pid1, pid2 ranks higher (return positive)
        if (pid2, pid1) in who_beat_who:
            return 1
            
        # 3. Set Wins (Higher is better)
        if s1['set_wins'] != s2['set_wins']:
            return s2['set_wins'] - s1['set_wins']
            
        # 4. Set Losses (Lower is better)
        if s1['set_losses'] != s2['set_losses']:
            return s1['set_losses'] - s2['set_losses']
            
        return 0
    
    order = sorted(stats.keys(), key=cmp_to_key(compare_players))
    
    id_to_name = {pid: info.get('name') for pid, info in participants_data.items()}
    names = [id_to_name.get(pid) for pid in order[:3]]
    while len(names) < 3:
        names.append(None)
    return tuple(names)

def determine_non_elimination_podium(tournament_type, matches, participants, participants_data):
    f1, f2, f3 = determine_podium_from_final_ranks(participants, participants_data)
    if f1 or f2 or f3:
        return f1, f2, f3
    return compute_round_robin_swiss_standings(matches, participants, participants_data)

def filter_placeholder_rounds(rounds_by_round):
    """Filter out rounds where all matches have only placeholder players."""
    filtered_rounds = {}
    
    for round_num, matches_in_round in rounds_by_round.items():
        all_placeholders = True
        
        if not matches_in_round:
            # Empty round, not considered "all placeholders"
            all_placeholders = False
        else:
            for match in matches_in_round:
                p1 = match.get('player1', {})
                p2 = match.get('player2', {})
                
                p1_name = p1.get('name', '')
                p2_name = p2.get('name', '')
                
                p1_is_placeholder = (p1_name == 'BYE / Open' or p1_name == 'To Be Determined')
                p2_is_placeholder = (p2_name == 'BYE / Open' or p2_name == 'To Be Determined')
                
                if not (p1_is_placeholder and p2_is_placeholder):
                    all_placeholders = False
                    break
        
        if not all_placeholders:
            filtered_rounds[round_num] = matches_in_round
    
    return filtered_rounds

def build_group_stage_data(matches, participants_data, player_colors, display_settings, tournament_name, slug):
    """Build template data dict for group stage display.

    Filters raw matches by group_id, applies sliding window via filter_rounds_to_display(),
    and returns the data dict for render_template('tournament.html', data=...).
    """
    # Filter raw matches first — group_id is NOT on processed matches
    raw_group_matches = [m for m in matches if m.get('group_id') is not None]
    group_processed = [process_match_detailed(m, participants_data) for m in raw_group_matches]

    # Inject group_id back (process_match_detailed doesn't preserve it)
    for pm, raw in zip(group_processed, raw_group_matches):
        pm['group_id'] = raw.get('group_id')

    # Get sliding window of rounds to display
    group_potential_rounds = get_potential_round_numbers(group_processed)
    group_display_rounds = filter_rounds_to_display(group_potential_rounds, group_processed)

    # Handle edge case: filter_rounds_to_display returns [] when no matches have participants yet
    # Spec: "at tournament start show rounds 1 and 2" — fall back to first two potential rounds
    if not group_display_rounds and group_potential_rounds:
        group_display_rounds = group_potential_rounds[:2] if len(group_potential_rounds) >= 2 else group_potential_rounds[:1]

    display_rounds_set = set(group_display_rounds)

    # Map numeric group_ids to letter labels (A, B, C, ...)
    all_group_ids = sorted(set(m['group_id'] for m in group_processed if m.get('group_id') is not None))
    group_id_to_label = {gid: chr(ord('A') + i) for i, gid in enumerate(all_group_ids)}

    # Build columns keyed by (round, group_label)
    columns = {}
    for m in group_processed:
        if m['round'] not in display_rounds_set:
            continue
        label = group_id_to_label.get(m.get('group_id'), '?')
        key = (m['round'], label)
        columns.setdefault(key, []).append(m)

    # Build per-round dicts of columns
    rounds_dict = {}
    for (r, label), matches in sorted(columns.items()):
        rounds_dict.setdefault(r, {})[label] = matches

    # Canonical group labels come from round 1 (the earliest displayed round)
    sorted_rounds = sorted(rounds_dict.keys())
    canonical_labels = sorted(rounds_dict[sorted_rounds[0]].keys()) if sorted_rounds else []

    def use_compact(matches):
        """Compact for past (all complete) or future (no open/underway) rounds."""
        return not any(m['state'] == 'open' for m in matches)

    # Build group_rounds with consistent columns across every round
    group_rounds = []
    for r in sorted_rounds:
        cols_for_round = rounds_dict[r]
        padded = []
        for label in canonical_labels:
            matches = cols_for_round.get(label, [])
            padded.append({
                'round': r,
                'group_label': label,
                'matches': matches,
                'use_compact': use_compact(matches),
                'is_placeholder': len(matches) == 0,
            })
        group_rounds.append({'round': r, 'columns': padded})

    return {
        'tournament_name': tournament_name,
        'slug': slug,
        'background_color': display_settings['background_color'],
        'title_color': display_settings['title_color'],
        'round_title_bg_color': display_settings['round_title_bg_color'],
        'player_colors': player_colors,
        'is_group_stage': True,
        'group_rounds': group_rounds,
        'error': None,
    }


def build_bracket_data(tournament_name, slug, display_settings, tournament_data, matches, participants):
    """Template data for one tournament's bracket page."""
    color_palette = COLOR_PALETTE

    # Process participants
    participants_data = create_participants_lookup(participants)
    player_colors = assign_player_colors(participants, color_palette)
    
    # Process matches with detailed player information
    processed_matches = [process_match_detailed(match, participants_data) for match in matches]
    
    # Determine match relationships for bracket connections
    for pm in processed_matches:
        for other_match in processed_matches:
            if other_match['id'] == pm['id']:
                continue
            
            # Check if pm feeds into other_match
            if (other_match.get('player1_prereq_match_id') == pm['id'] or 
                other_match.get('player2_prereq_match_id') == pm['id']):
                pm['feeds_into_match_id'] = other_match['id']
                break
        
        # Set has_outgoing_line if match feeds into another and has a winner
        if pm['feeds_into_match_id'] and pm['winner_id']:
            pm['has_outgoing_line'] = True
    
    # Detect group stage phase
    group_stages_enabled = tournament_data.get('group_stages_enabled', False)
    tournament_state = tournament_data.get('state')
    is_group_stage = bool(group_stages_enabled) and tournament_state == 'group_stages_underway'

    if is_group_stage:
        return build_group_stage_data(matches, participants_data, player_colors, display_settings, tournament_name, slug)

    # For tournaments with group stages now in bracket stage, exclude group stage matches
    # (group_id is only on raw matches; processed_matches lose it after process_match_detailed)
    if bool(group_stages_enabled):
        group_match_ids = {m['id'] for m in matches if m.get('group_id') is not None}
        processed_matches = [m for m in processed_matches if m['id'] not in group_match_ids]

    # Categorize matches by bracket type
    all_winner_bracket_matches = [m for m in processed_matches if m['bracket'] == 'winners' and m['round'] != 999]
    all_loser_bracket_matches = [m for m in processed_matches if m['bracket'] == 'losers']
    grand_finals_matches = [m for m in processed_matches if m['bracket'] == 'winners' and m['round'] == 999]
    
    # Determine rounds to display for each bracket
    potential_winner_rounds = get_potential_round_numbers(all_winner_bracket_matches)
    potential_loser_rounds = get_potential_round_numbers(all_loser_bracket_matches)
    
    displayable_winner_round_nums = filter_rounds_to_display(potential_winner_rounds, all_winner_bracket_matches)
    displayable_loser_round_nums = filter_rounds_to_display(potential_loser_rounds, all_loser_bracket_matches)
    
    # Determine if losers bracket should be hidden
    hide_losers_bracket = False
    if all_loser_bracket_matches:
        all_losers_complete = all(match.get('state') == 'complete' for match in all_loser_bracket_matches)
        if all_losers_complete:
            hide_losers_bracket = True
    
    # Determine podium finishers
    first_place_name, second_place_name, third_place_name = determine_podium_finishers(processed_matches, participants_data)
    tournament_type = (tournament_data.get('tournament_type') or '').lower()
    if tournament_type in ['round robin', 'round_robin', 'swiss']:
        alt_first, alt_second, alt_third = determine_non_elimination_podium(tournament_type, matches, participants, participants_data)
        if alt_first:
            first_place_name = alt_first
        if alt_second:
            second_place_name = alt_second
        if alt_third:
            third_place_name = alt_third

    # For group-stage → single elimination brackets, infer 3rd place from semifinal losers
    # before Challonge sets final_rank (which only happens when the tournament is fully complete)
    if not third_place_name and group_stages_enabled and tournament_type == 'single elimination':
        bracket_matches = [m for m in processed_matches if m['bracket'] == 'winners']
        if bracket_matches:
            max_round = max(m['round'] for m in bracket_matches)
            semi_losers = [
                participants_data[m['loser_id']]['name']
                for m in bracket_matches
                if m['round'] == max_round - 1 and m.get('loser_id') and m['loser_id'] in participants_data
            ]
            if semi_losers:
                third_place_name = ' / '.join(semi_losers)

    # Count participants who are still alive in the tournament (in open matches)
    # This is more accurate than counting all participants with no final_rank
    alive_participants = set()
    
    # Find all open and pending matches and collect participants who are still competing
    # Include matches from both winners and losers brackets
    for match in processed_matches:
        if match['state'] in ['open', 'pending']:
            # Add both players from open and pending matches (if they exist and aren't TBD)
            # Pending matches also have alive participants waiting to compete
            if match.get('player1') and match['player1'].get('id') and not match['player1'].get('is_tbd'):
                alive_participants.add(match['player1']['id'])
            if match.get('player2') and match['player2'].get('id') and not match['player2'].get('is_tbd'):
                alive_participants.add(match['player2']['id'])
    
    alive_participant_count = len(alive_participants)
    
    is_final_stage = False
    is_three_competitor_stage = False
    second_chance_matches = []
    is_tournament_complete = False
    # tournament_data is the raw tournament dict from Challonge
    # Check for tournament completion FIRST (highest precedence)
    # Tournament is complete if state is 'complete' OR if there are no more active matches (0 or 1 alive participants)
    if tournament_data.get('state') == 'complete' or alive_participant_count <= 1:
        is_tournament_complete = True
    # Check for three-competitor stage (takes precedence over normal bracket)
    # For Swiss/Round Robin: fewer than 3 competitors (< 3, so <= 2)
    # For Elimination: 6 or fewer competitors (<= 6)
    participants_threshold = 6
    if tournament_type in ['round robin', 'round_robin', 'swiss']:
        participants_threshold = 2
        
    elif alive_participant_count <= participants_threshold and tournament_data.get('state') in ['underway', 'awaiting_review']:
        is_three_competitor_stage = True
        # For this stage, we want a flat list of all active loser's bracket matches
        for round_num in displayable_loser_round_nums:
            for match in all_loser_bracket_matches:
                if match['round'] == round_num and match['state'] in ['open', 'pending']:
                    second_chance_matches.append(match)
    # Old final stage logic is now completely replaced by three-competitor stage

    # With 8 or fewer robots left in an elimination bracket, the "Road to the final" screen replaces
    # both the full bracket and the three-competitor stage.
    endgame_screen = None
    if not is_tournament_complete and tournament_data.get('state') in ['underway', 'awaiting_review']:
        endgame_screen = endgame.build(matches, participants, tournament_type,
                                       display_settings['round_title_bg_color'])
        if endgame_screen:
            is_three_competitor_stage = False

    # Once it's decided, the final results in the same style.
    final_screen = None
    if is_tournament_complete:
        pending = (tournament_type in ['round robin', 'round_robin', 'swiss']
                   and tournament_data.get('state') != 'complete')
        final_screen = endgame.final_results(matches, participants, tournament_type,
                                             (first_place_name, second_place_name, third_place_name),
                                             display_settings['round_title_bg_color'],
                                             note='Pending finalization' if pending else None)

    # Organize matches by bracket and round for display
    winner_matches_by_round = {r: [] for r in displayable_winner_round_nums}
    for m in all_winner_bracket_matches:
        if m['round'] in winner_matches_by_round:
            winner_matches_by_round[m['round']].append(m)
    
    loser_matches_by_round = {r: [] for r in displayable_loser_round_nums}
    for m in all_loser_bracket_matches:
        if m['round'] in loser_matches_by_round:
            loser_matches_by_round[m['round']].append(m)
    
    # Filter out rounds where all matches consist only of placeholders
    filtered_winner_matches_by_round = filter_placeholder_rounds(winner_matches_by_round)
    filtered_loser_matches_by_round = filter_placeholder_rounds(loser_matches_by_round)
    # Full size of each losers round, before empty matches are dropped below, so a round keeps one
    # layout (single or double line) while it fills instead of switching as robots arrive.
    loser_round_sizes = {r: len(ms) for r, ms in filtered_loser_matches_by_round.items()}
    # Losers rounds fill up as the winners side plays, so early on most of their matches have nobody in
    # them yet. Leave those out instead of drawing empty rows; each round grows as robots drop down.
    filtered_loser_matches_by_round = {
        r: [m for m in ms if not (m['player1'].get('is_tbd') and m['player2'].get('is_tbd'))]
        for r, ms in filtered_loser_matches_by_round.items()
    }
    filtered_loser_matches_by_round = {r: ms for r, ms in filtered_loser_matches_by_round.items() if ms}
    
    # Prepare template data
    template_data = {
        'tournament_name': tournament_name,
        'slug': slug,
        'background_color': display_settings['background_color'],
        'title_color': display_settings['title_color'],
        'round_title_bg_color': display_settings['round_title_bg_color'],
        'player_colors': player_colors,
        'winner_rounds_ordered': sorted(list(filtered_winner_matches_by_round.keys())),
        'winner_matches_by_round': filtered_winner_matches_by_round,
        'loser_rounds_ordered': sorted(list(filtered_loser_matches_by_round.keys())),
        'loser_matches_by_round': filtered_loser_matches_by_round,
        'loser_round_sizes': loser_round_sizes,
        'grand_finals_matches': grand_finals_matches,
        'hide_losers_bracket': hide_losers_bracket,
        'is_final_stage': is_final_stage,
        'is_three_competitor_stage': is_three_competitor_stage,
        'endgame': endgame_screen,
        'final_screen': final_screen,
        'second_chance_matches': second_chance_matches,
        'is_tournament_complete': is_tournament_complete,
        'third_place_finisher_name': third_place_name,
        'first_place_name': first_place_name,
        'second_place_name': second_place_name,
        'tournament_type': tournament_type,
        'tournament_state': tournament_data.get('state'),
        'error': None
    }

    return template_data
