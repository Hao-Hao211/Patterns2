"""Scoring functions for Patterns2 - single source of truth.

Used by both main.py (game engine) and leaderboard.py (ranking calculations).
"""

from typing import List, Dict, Optional, Tuple


def calculate_score(master_pattern: List[List[str]], guess: List[List[str]],
                    queried_cells: List[Dict[str, int]], grid_size: int) -> int:
    """Calculate a player's score.

    +1 for each correct unqueried cell, -1 for each incorrect unqueried cell.
    Queried cells contribute 0.
    """
    if not guess or len(guess) != grid_size:
        return 0

    score = 0
    queried_positions = {(cell['row'], cell['col']) for cell in queried_cells}

    for i in range(grid_size):
        for j in range(grid_size):
            if (i, j) not in queried_positions:
                if i < len(guess) and j < len(guess[i]) and i < len(master_pattern) and j < len(master_pattern[i]):
                    if guess[i][j] == master_pattern[i][j]:
                        score += 1
                    else:
                        score -= 1

    return score


def calculate_ranks(scores: List[Tuple[str, int]]) -> Dict[str, int]:
    """Calculate rankings from scores (higher score = lower rank number, ties share rank)."""
    sorted_scores = sorted(scores, key=lambda x: x[1], reverse=True)

    ranks = {}
    current_rank = 1
    prev_score = None

    for i, (participant_id, score) in enumerate(sorted_scores):
        if prev_score is not None and score < prev_score:
            current_rank = i + 1
        ranks[participant_id] = current_rank
        prev_score = score

    return ranks


def analyze_action_log(action_log: Optional[List[str]]) -> Tuple[int, int, int]:
    """Analyze action_log to extract observation count, rounds, and quit status.

    Returns:
        tuple: (observation_count, observation_rounds, did_quit)
    """
    if not action_log:
        return 0, 0, 0

    observation_count = 0
    observation_rounds = 0
    did_quit = 0

    for log_entry in action_log:
        if "Observed cells:" in log_entry:
            observation_rounds += 1
            try:
                cells_part = log_entry.split("Observed cells:")[1].split(".")[0]
                cells = [c.strip() for c in cells_part.split(",") if c.strip()]
                observation_count += len(cells)
            except Exception:
                pass

        if "Gave up the game" in log_entry or "give_up" in log_entry.lower():
            did_quit = 1

    return observation_count, observation_rounds, did_quit


def calculate_designer_score(player_scores: List[float], num_dropouts: int = 0) -> float:
    """Calculate designer score using the simplified formula.

    Designer Score = 2 * (max(player_scores) - min(player_scores)) - dropout_penalty

    Dropout penalty:
        -5 for the first dropout, -10 for each additional.
        0 dropouts: 0 penalty
        1 dropout: -5
        2 dropouts: -5 + -10 = -15
        3 dropouts: -5 + -10 + -10 = -25

    Args:
        player_scores: List of player scores (raw ints/floats)
        num_dropouts: Number of players who quit/gave up

    Returns:
        Single float designer score
    """
    if not player_scores:
        return 0.0

    best = max(player_scores)
    worst = min(player_scores)
    score = 2.0 * (best - worst)

    # Apply dropout penalty
    if num_dropouts > 0:
        penalty = 5 + (num_dropouts - 1) * 10
        score -= penalty

    return score


def calculate_revised_designer_score(player_scores: List[float], num_dropouts: int = 0) -> float:
    """Calculate Revised Designer Score.

    Revised Designer Score = mean_Sci + 0.5 * (max_Sci - min_Sci) - Q

    Q (dropout penalty):
        Q = 0 if num_dropouts == 0
        Q = 5 + 10 * (num_dropouts - 1) otherwise

    Args:
        player_scores: List of player (scientist) scores
        num_dropouts: Number of players who quit/gave up

    Returns:
        Single float revised designer score
    """
    if not player_scores:
        return 0.0

    mean_sci = sum(player_scores) / len(player_scores)
    max_sci = max(player_scores)
    min_sci = min(player_scores)
    score = mean_sci + 0.5 * (max_sci - min_sci)

    # Apply dropout penalty Q
    if num_dropouts > 0:
        q = 5 + (num_dropouts - 1) * 10
        score -= q

    return score
