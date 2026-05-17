import json
import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import trueskill

from scoring import calculate_designer_score, calculate_revised_designer_score

logger = logging.getLogger(__name__)

# Static OpenRouter price reference (USD per 1M tokens, as of 2026-Q1).
# Used as a fallback when OpenRouter does not supply pricing in /models.
OPENROUTER_PRICING = {
    # OpenAI models via OpenRouter
    'openai/gpt-5-nano': {'input': 0.05, 'output': 0.40},
    'openai/gpt-5-mini': {'input': 0.25, 'output': 2.00},
    'openai/gpt-5': {'input': 0.625, 'output': 5.50},  # 50% off
    'openai/gpt-5-chat': {'input': 1.25, 'output': 10.00},
    'openai/o3-mini-high': {'input': 1.10, 'output': 4.40},
    'openai/o3-mini': {'input': 1.10, 'output': 4.40},
    'openai/o3': {'input': 2.00, 'output': 8.00},
    'openai/o3-pro': {'input': 20.00, 'output': 80.00},
    'openai/o4-mini-high': {'input': 1.10, 'output': 4.40},
    'openai/o4-mini': {'input': 1.10, 'output': 4.40},
    'openai/gpt-4o': {'input': 2.50, 'output': 10.00},
    'openai/gpt-4o-mini': {'input': 0.15, 'output': 0.60},
    'openai/gpt-4-turbo': {'input': 10.00, 'output': 30.00},
    'openai/gpt-3.5-turbo': {'input': 0.50, 'output': 1.50},
    'openai/o1': {'input': 15.00, 'output': 60.00},
    'openai/o1-mini': {'input': 1.10, 'output': 4.40},
    'openai/o1-preview': {'input': 15.00, 'output': 60.00},

    # Anthropic models
    'anthropic/claude-3.5-haiku-20241022': {'input': 0.80, 'output': 4.00},
    'anthropic/claude-sonnet-4': {'input': 3.00, 'output': 15.00},
    'anthropic/claude-3.5-sonnet-20240620': {'input': 3.00, 'output': 15.00},
    'anthropic/claude-3.5-sonnet': {'input': 3.00, 'output': 15.00},
    'anthropic/claude-opus-4.1': {'input': 15.00, 'output': 75.00},
    'anthropic/claude-opus-4': {'input': 15.00, 'output': 75.00},
    'anthropic/claude-3.7-sonnet': {'input': 3.00, 'output': 15.00},

    # xAI models
    'x-ai/grok-4': {'input': 3.00, 'output': 15.00},

    # Google models
    'google/gemini-flash-1.5': {'input': 0.075, 'output': 0.30},
    'google/gemini-2.5-pro': {'input': 1.25, 'output': 10},

    # Meta models
    'meta-llama/llama-4-scout': {'input': 0.08, 'output': 0.30},
    'meta-llama/llama-4-maverick': {'input': 0.15, 'output': 0.60},


    # Deepseek models
    'deepseek/deepseek-r1-0528': {'input': 0.40, 'output': 1.75},
    'deepseek/deepseek-chat': {'input': 0.30, 'output': 1.20},
}


def get_model_pricing(model_name: str) -> Optional[Dict[str, float]]:
    """Look up per-1M-token pricing for an OpenRouter model identifier."""
    if model_name in OPENROUTER_PRICING:
        pricing = OPENROUTER_PRICING[model_name]
        logger.debug(
            "Pricing for %s: input $%s/1M, output $%s/1M",
            model_name, pricing["input"], pricing["output"],
        )
        return pricing

    # Fallback: rough average for unknown models.
    logger.warning("No pricing found for %s — using defaults", model_name)
    return {"input": 1.00, "output": 3.00}


def calculate_cost(input_tokens: int, output_tokens: int, model_name: str) -> float:
    """Calculate cost for a model based on token usage with enhanced logging"""
    logger.debug(f"Calculating cost for model {model_name}: input {input_tokens} tokens, output {output_tokens} tokens")

    pricing = get_model_pricing(model_name)
    if not pricing:
        logger.warning(f"Cannot get pricing for model {model_name}")
        return 0.0

    # Convert to cost (pricing is per 1M tokens)
    input_cost = (input_tokens / 1_000_000) * pricing['input']
    output_cost = (output_tokens / 1_000_000) * pricing['output']
    total_cost = input_cost + output_cost

    logger.debug(
        f"Model {model_name} cost: input ${input_cost:.6f}, output ${output_cost:.6f}, total ${total_cost:.6f}")

    return total_cost


@dataclass
class PlayerStats:
    """Player statistics data."""
    model_name: str
    model_params: Optional[Dict[str, Any]] = None
    games_as_player: int = 0
    games_as_designer: int = 0
    total_score_as_player: int = 0
    theoretical_max_scientist_score: int = 0  # Sum of grid_size^2 for all games as player
    total_score_as_designer: float = 0.0
    total_revised_score_as_designer: float = 0.0
    wins_as_player: int = 0
    wins_as_designer: int = 0
    overall_wins: int = 0  # Overall wins (highest score in a game)
    total_games: int = 0  # Total games (as both player and designer)
    elo_rating: float = 1500.0
    trueskill_rating: Optional[trueskill.Rating] = None
    total_cost: float = 0.0  # Total cost
    total_input_tokens: int = 0  # Total input tokens
    total_output_tokens: int = 0  # Total output tokens

    def __post_init__(self):
        if self.trueskill_rating is None:
            # Create default rating using official TrueSkill library
            env = trueskill.TrueSkill(draw_probability=0.1)
            self.trueskill_rating = env.create_rating()

    @property
    def avg_score_as_player(self) -> float:
        return self.total_score_as_player / self.games_as_player if self.games_as_player > 0 else 0.0

    @property
    def avg_score_as_designer(self) -> float:
        return self.total_score_as_designer / self.games_as_designer if self.games_as_designer > 0 else 0.0

    @property
    def avg_revised_score_as_designer(self) -> float:
        return self.total_revised_score_as_designer / self.games_as_designer if self.games_as_designer > 0 else 0.0

    @property
    def win_rate_as_player(self) -> float:
        return (self.wins_as_player / self.games_as_player * 100) if self.games_as_player > 0 else 0.0

    @property
    def win_rate_as_designer(self) -> float:
        return (self.wins_as_designer / self.games_as_designer * 100) if self.games_as_designer > 0 else 0.0

    @property
    def overall_win_rate(self) -> float:
        """Overall win rate: rate of achieving highest score across all participated games."""
        return (self.overall_wins / self.total_games * 100) if self.total_games > 0 else 0.0

    @property
    def conservative_trueskill_rating(self) -> float:
        """TrueSkill conservative rating: mu - 3*sigma."""
        if self.trueskill_rating:
            return self.trueskill_rating.mu - 3 * self.trueskill_rating.sigma
        return 0.0

    @property
    def cost_per_game(self) -> float:
        """Average cost per game."""
        return self.total_cost / self.total_games if self.total_games > 0 else 0.0


# Scoring functions (calculate_designer_score)
# are imported from scoring.py - single source of truth


class ELOCalculator:
    """ELO rating calculator."""

    @staticmethod
    def expected_score(rating_a: float, rating_b: float) -> float:
        """Calculate expected score."""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))

    @staticmethod
    def update_rating(rating: float, expected: float, actual: float, k_factor: float = 32) -> float:
        """Update ELO rating."""
        return rating + k_factor * (actual - expected)

    @classmethod
    def calculate_k_factor(cls, num_players: int) -> float:
        """Dynamically calculate K factor based on player count: K = 32 / log(N+1)."""
        if num_players <= 1:
            return 32.0
        return 32.0 / math.log(num_players + 1)

    @classmethod
    def update_ratings_multiplayer(cls, players: List[Tuple[str, float, int]]) -> Dict[str, float]:
        """
        Multiplayer ELO rating update
        players: [(player_id, current_rating, final_score), ...]
        Returns: {player_id: new_rating, ...}
        """
        if len(players) < 2:
            return {player_id: rating for player_id, rating, _ in players}

        # Dynamically calculate K factor
        k_factor = cls.calculate_k_factor(len(players))

        # Sort by score, higher score ranks higher
        sorted_players = sorted(players, key=lambda x: x[2], reverse=True)
        n = len(players)
        new_ratings = {}

        for i, (player_id, rating, score) in enumerate(sorted_players):
            total_expected = 0.0
            actual_score = 0.0

            # Calculate expected and actual scores vs all other players
            for j, (other_id, other_rating, other_score) in enumerate(sorted_players):
                if i != j:
                    expected = cls.expected_score(rating, other_rating)
                    total_expected += expected

                    # Actual score: win=1, draw=0.5, loss=0
                    if score > other_score:
                        actual_score += 1.0
                    elif score == other_score:
                        actual_score += 0.5

            # Normalize to 0-1 range
            if n > 1:
                total_expected /= (n - 1)
                actual_score /= (n - 1)

            new_rating = cls.update_rating(rating, total_expected, actual_score, k_factor)
            new_ratings[player_id] = max(100, new_rating)  # Minimum rating 100

        return new_ratings


class TrueSkillCalculator:
    """TrueSkill rating calculator (using official library)."""

    def __init__(self):
        # Create TrueSkill environment
        self.env = trueskill.TrueSkill(draw_probability=0.1)

    def create_rating(self) -> trueskill.Rating:
        """Create new rating."""
        return self.env.create_rating()

    def update_ratings_multiplayer(self, players: List[Tuple[str, trueskill.Rating, int]]) -> Dict[
        str, trueskill.Rating]:
        """
        Multiplayer TrueSkill rating update
        players: [(player_id, current_rating, final_score), ...]
        Returns: {player_id: new_rating, ...}
        """
        if len(players) < 2:
            return {player_id: rating for player_id, rating, _ in players}

        # Sort by score, create rankings
        sorted_players = sorted(players, key=lambda x: x[2], reverse=True)

        # Create rank list (tied players share ranks)
        ranks = []
        current_rank = 0
        prev_score = None

        for i, (player_id, rating, score) in enumerate(sorted_players):
            if prev_score is None or score != prev_score:
                current_rank = i
            ranks.append(current_rank)
            prev_score = score

        # Create rating_groups - each player as individual team
        rating_groups = [[rating] for _, rating, _ in sorted_players]

        # Update ratings using TrueSkill
        new_rating_groups = self.env.rate(rating_groups, ranks=ranks)

        # Build result dictionary
        new_ratings = {}
        for i, (player_id, _, _) in enumerate(sorted_players):
            new_ratings[player_id] = new_rating_groups[i][0]  # Extract the single rating from team

        return new_ratings


class LeaderboardCalculator:
    """Leaderboard calculator."""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.elo_calculator = ELOCalculator()
        self.trueskill_calculator = TrueSkillCalculator()

    async def calculate_leaderboard(self, test_set_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Calculate leaderboard
        test_set_id: If provided, calculate only for this test set; otherwise all data
        """
        try:
            logger.info(f"Starting leaderboard calculation, test_set_id: {test_set_id}")

            # Fetch game data
            games_data = await self._fetch_games_data(test_set_id)

            if not games_data:
                logger.warning("No game data found")
                return []

            logger.info(f"Fetched {len(games_data)} game records")

            # Initialize player statistics
            player_stats = defaultdict(lambda: PlayerStats(""))

            # Collect basic statistics
            await self._collect_basic_stats(games_data, player_stats)

            # Calculate ELO and TrueSkill ratings
            await self._calculate_ratings(games_data, player_stats)

            # Convert to leaderboard format
            leaderboard = self._format_leaderboard(player_stats)

            # Save leaderboard data to database
            await self._save_leaderboard_to_db(leaderboard, test_set_id)

            logger.info(f"Leaderboard calculation complete, {len(leaderboard)} entries")
            return leaderboard

        except Exception as e:
            logger.error(f"Leaderboard calculation failed: {e}", exc_info=True)
            return []

    async def _fetch_games_data(self, test_set_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch game data from game_analytics table (unified source of truth)."""
        try:
            async with self.db_pool.acquire() as conn:
                # Check game_analytics has data
                total_games = await conn.fetchval("SELECT COUNT(DISTINCT game_id) FROM game_analytics")
                logger.info(f"Total {total_games} games in game_analytics")

                if total_games == 0:
                    logger.warning("No game data in game_analytics")
                    return []

                players_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM game_analytics WHERE participant_role = 'player'")
                logger.info(f"game_analytics has {players_count} player records")

                token_data_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM game_analytics WHERE participant_role = 'player' AND (input_tokens > 0 OR output_tokens > 0)")
                logger.info(f"game_analytics has {token_data_count} player records with token data")

                # Fetch all player rows (LLM + Human) from game_analytics
                if test_set_id:
                    player_query = """
                        SELECT game_id, grid_size, created_at,
                               participant_name, participant_type, participant_llm_model, participant_llm_model_params,
                               final_score, input_tokens, output_tokens, did_quit
                        FROM game_analytics
                        WHERE test_set_id = $1
                          AND participant_role = 'player'
                        ORDER BY created_at, game_id
                    """
                    player_rows = await conn.fetch(player_query, test_set_id)
                    logger.info(f"Test set {test_set_id}: {len(player_rows)} player records found")
                else:
                    player_query = """
                        SELECT game_id, grid_size, created_at,
                               participant_name, participant_type, participant_llm_model, participant_llm_model_params,
                               final_score, input_tokens, output_tokens, did_quit
                        FROM game_analytics
                        WHERE participant_role = 'player'
                        ORDER BY created_at, game_id
                    """
                    player_rows = await conn.fetch(player_query)
                    logger.info(f"All games query: {len(player_rows)} player records found")

                if not player_rows:
                    logger.warning("No player game records found")
                    return []

                # Fetch all designer rows from game_analytics (keyed by game_id)
                if test_set_id:
                    designer_query = """
                        SELECT game_id, participant_type, participant_llm_model, participant_llm_model_params,
                               input_tokens, output_tokens
                        FROM game_analytics
                        WHERE test_set_id = $1
                          AND participant_role = 'designer'
                    """
                    designer_rows = await conn.fetch(designer_query, test_set_id)
                else:
                    designer_query = """
                        SELECT game_id, participant_type, participant_llm_model, participant_llm_model_params,
                               input_tokens, output_tokens
                        FROM game_analytics
                        WHERE participant_role = 'designer'
                    """
                    designer_rows = await conn.fetch(designer_query)

                # Build designer info lookup by game_id
                designer_info = {}
                for drow in designer_rows:
                    gid = str(drow['game_id'])
                    designer_params = None
                    if drow['participant_llm_model_params']:
                        try:
                            if isinstance(drow['participant_llm_model_params'], str):
                                designer_params = json.loads(drow['participant_llm_model_params'])
                            else:
                                designer_params = drow['participant_llm_model_params']
                        except Exception as e:
                            logger.warning(f"Failed to parse designer params: {e}")
                    designer_info[gid] = {
                        'designer_type': drow['participant_type'],  # 'LLM'
                        'designer_llm_model': drow['participant_llm_model'],
                        'designer_llm_model_params': designer_params,
                    }

                # Group player rows by game ID
                games_by_id = {}
                for row in player_rows:
                    game_id = str(row['game_id'])

                    # Parse player model params
                    player_params = None
                    if row['participant_llm_model_params']:
                        try:
                            if isinstance(row['participant_llm_model_params'], str):
                                player_params = json.loads(row['participant_llm_model_params'])
                            else:
                                player_params = row['participant_llm_model_params']
                        except Exception as e:
                            logger.warning(f"Failed to parse player params: {e}")
                            player_params = None

                    input_tokens = row.get('input_tokens') or 0
                    output_tokens = row.get('output_tokens') or 0

                    player_data = {
                        'player_name_in_game': row['participant_name'],
                        'player_type': row['participant_type'],
                        'player_llm_model': row['participant_llm_model'],
                        'player_llm_model_params': player_params,
                        'final_score': row['final_score'] if row['final_score'] is not None else 0,
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens,
                        'did_quit': row.get('did_quit', 0) or 0,
                    }

                    if game_id not in games_by_id:
                        # Get designer info for this game (if exists)
                        d_info = designer_info.get(game_id, {})
                        games_by_id[game_id] = {
                            'id': game_id,
                            'grid_size': row['grid_size'],
                            'designer_type': d_info.get('designer_type', 'Human'),
                            'designer_llm_model': d_info.get('designer_llm_model'),
                            'designer_llm_model_params': d_info.get('designer_llm_model_params'),
                            'created_at': row['created_at'],
                            'players': []
                        }

                    games_by_id[game_id]['players'].append(player_data)

                # Convert to list
                games_data = list(games_by_id.values())

                logger.info(f"Successfully processed {len(games_data)} games")

                # Print debug info
                for i, game in enumerate(games_data[:3]):
                    logger.info(f"Game {i + 1}: {len(game['players'])} players, designer: {game['designer_type']}")
                    for j, player in enumerate(game['players']):
                        logger.info(
                            f"  Player {j + 1}: {player['player_llm_model']}, score: {player['final_score']}, tokens: {player['input_tokens']}/{player['output_tokens']}")

                return games_data

        except Exception as e:
            logger.error(f"Failed to fetch game data: {e}", exc_info=True)
            return []

    async def _collect_basic_stats(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """Collect basic statistics - read designer scores from game_analytics."""
        logger.info("Collecting basic statistics")

        for game in games_data:
            try:
                # Collect all participant scores for this game
                all_participants_scores = []  # Stores (participant_key, score, type)
                designer_key = None
                designer_score = 0

                # Process designer - read scores from game_analytics
                if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                    designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])
                    if designer_key not in player_stats:
                        player_stats[designer_key] = PlayerStats(
                            model_name=game['designer_llm_model'],
                            model_params=game['designer_llm_model_params']
                        )

                    player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                    num_dropouts = sum(1 for p in game['players'] if p.get('did_quit', 0))

                    if len(player_scores) >= 1:
                        new_designer_score = calculate_designer_score(player_scores, num_dropouts)
                        new_revised_designer_score = calculate_revised_designer_score(player_scores, num_dropouts)

                        player_stats[designer_key].games_as_designer += 1
                        player_stats[designer_key].total_score_as_designer += new_designer_score
                        player_stats[designer_key].total_revised_score_as_designer += new_revised_designer_score
                        player_stats[designer_key].total_games += 1

                        all_participants_scores.append((designer_key, new_designer_score, 'designer'))
                        designer_score = new_designer_score

                        logger.debug(
                            f"Game {game['id']}: designer {designer_key} score {new_designer_score:.2f} revised {new_revised_designer_score:.2f} (dropouts: {num_dropouts})")

                # Process players - enhanced version ensuring correct cost calculation
                valid_players_for_win_rate = []  # Stores (player_key, final_score) for win rate calculation
                for player in game['players']:
                    if player['player_llm_model'] and player['final_score'] is not None:
                        player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])

                        if player_key not in player_stats:
                            player_stats[player_key] = PlayerStats(
                                model_name=player['player_llm_model'],
                                model_params=player['player_llm_model_params']
                            )

                        player_stats[player_key].games_as_player += 1
                        player_stats[player_key].total_score_as_player += player['final_score']
                        gs = game.get('grid_size', 6)
                        player_stats[player_key].theoretical_max_scientist_score += gs * gs
                        player_stats[player_key].total_games += 1

                        # Get token data and calculate cost (humans have 0 tokens/cost)
                        input_tokens = player.get('input_tokens', 0) or 0
                        output_tokens = player.get('output_tokens', 0) or 0

                        logger.debug(f"Player {player_key} token data: input {input_tokens}, output {output_tokens}")

                        # Calculate cost - call even if tokens are 0 for logging
                        player_cost = calculate_cost(input_tokens, output_tokens, player['player_llm_model'])

                        player_stats[player_key].total_input_tokens += input_tokens
                        player_stats[player_key].total_output_tokens += output_tokens
                        player_stats[player_key].total_cost += player_cost

                        if player_cost > 0:
                            logger.info(
                                f"Game {game['id']}: player {player_key} score {player['final_score']}, tokens: {input_tokens}/{output_tokens}, cost ${player_cost:.6f}")
                        else:
                            logger.debug(
                                f"Game {game['id']}: player {player_key} score {player['final_score']}, tokens: {input_tokens}/{output_tokens}, cost calculated as 0")

                        # Add to valid player list for win rate calculation
                        valid_players_for_win_rate.append((player_key, player['final_score']))
                        all_participants_scores.append((player_key, player['final_score'], 'player'))

                # Calculate player wins
                if valid_players_for_win_rate:
                    max_player_score = max(score for _, score in valid_players_for_win_rate)
                    # Find all highest scoring players (may include ties)
                    winners = [player_key for player_key, score in valid_players_for_win_rate if
                               score == max_player_score]

                    for winner_key in winners:
                        player_stats[winner_key].wins_as_player += 1
                        logger.debug(f"Game {game['id']}: player wins {winner_key} score {max_player_score}")

                # Calculate overall wins (highest score among all participants)
                if all_participants_scores:
                    max_overall_score = max(score for _, score, _ in all_participants_scores)
                    winners = [participant_key for participant_key, score, _ in all_participants_scores if
                               score == max_overall_score]

                    logger.debug(f"Game {game['id']}: highest score {max_overall_score}, winners {winners}")

                    for participant_key in winners:
                        player_stats[participant_key].overall_wins += 1
                        logger.debug(f"Game {game['id']}: overall win {participant_key} score {max_overall_score}")

            except Exception as e:
                logger.error(f"Failed to process game {game.get('id', 'unknown')}: {e}", exc_info=True)
                continue

        # Print statistics results
        logger.info(f"Stats complete: {len(player_stats)} models:")
        for key, stats in player_stats.items():
            logger.info(
                f"  {key}: player games:{stats.games_as_player}(wins:{stats.wins_as_player}), designer games:{stats.games_as_designer}(wins:{stats.wins_as_designer}), overall:{stats.overall_wins}/{stats.total_games}, tokens: {stats.total_input_tokens}/{stats.total_output_tokens}, cost:${stats.total_cost:.6f}")

    def _group_games_by_round(self, games_data: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Group games by rotation rounds
        Assumes games in the same round are close in time with the same designer
        """
        if not games_data:
            return []

        # Sort by time
        sorted_games = sorted(games_data, key=lambda x: x['created_at'])

        rounds = []
        current_round = []
        current_designers = set()

        for game in sorted_games:
            designer_key = None
            if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])

            # Start new round if empty or designer already appeared in current round
            if not current_round or (designer_key and designer_key in current_designers):
                if current_round:
                    rounds.append(current_round)
                current_round = [game]
                current_designers = {designer_key} if designer_key else set()
            else:
                current_round.append(game)
                if designer_key:
                    current_designers.add(designer_key)

        # Add the last round
        if current_round:
            rounds.append(current_round)

        logger.info(f"Game grouping complete, {len(rounds)} rounds, games per round: {[len(r) for r in rounds]}")
        return rounds

    async def _calculate_ratings(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """Calculate ELO and TrueSkill ratings."""
        logger.info("Starting rating calculations")

        # Process games in chronological order
        games_processed = 0
        for game in games_data:
            try:
                # Collect all participants (designer + players)
                all_participants_for_rating = []  # Stores (participant_key, score, type)

                # Add designer
                designer_key = None
                if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                    designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])
                    if designer_key in player_stats:
                        # Calculate designer score
                        player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                        if len(player_scores) >= 1:
                            num_dropouts = sum(1 for p in game['players'] if p.get('did_quit', 0))
                            designer_score = calculate_designer_score(player_scores, num_dropouts)
                            all_participants_for_rating.append((designer_key, designer_score, 'designer'))

                # Add players
                for player in game['players']:
                    if (player['player_llm_model'] and
                            player['final_score'] is not None):
                        player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])
                        if player_key in player_stats:
                            all_participants_for_rating.append((player_key, player['final_score'], 'player'))

                # Only process games with multiple participants
                if len(all_participants_for_rating) < 2:
                    continue

                # Prepare ELO calculation data
                elo_players = []
                trueskill_players = []

                for participant_key, score, _ in all_participants_for_rating:
                    elo_players.append((participant_key, player_stats[participant_key].elo_rating, score))
                    trueskill_players.append((participant_key, player_stats[participant_key].trueskill_rating, score))

                # Update ELO ratings
                if len(elo_players) >= 2:
                    new_elo_ratings = self.elo_calculator.update_ratings_multiplayer(elo_players)
                    for participant_key, new_rating in new_elo_ratings.items():
                        old_rating = player_stats[participant_key].elo_rating
                        player_stats[participant_key].elo_rating = new_rating
                        logger.debug(f"ELO update {participant_key}: {old_rating:.1f} -> {new_rating:.1f}")

                # Update TrueSkill ratings
                if len(trueskill_players) >= 2:
                    new_trueskill_ratings = self.trueskill_calculator.update_ratings_multiplayer(trueskill_players)
                    for participant_key, new_rating in new_trueskill_ratings.items():
                        old_rating = player_stats[participant_key].trueskill_rating
                        player_stats[participant_key].trueskill_rating = new_rating
                        logger.debug(
                            f"TrueSkill update {participant_key}: μ {old_rating.mu:.2f}->{new_rating.mu:.2f}, σ {old_rating.sigma:.2f}->{new_rating.sigma:.2f}")

                games_processed += 1

            except Exception as e:
                logger.error(f"Rating calculation failed: {e}", exc_info=True)
                continue

        logger.info(f"Rating calculation completed, processed {games_processed} games")

    def _get_player_key(self, model_name: str, model_params: Optional[Dict[str, Any]]) -> str:
        """Generate unique player key - improved version ensuring consistency."""
        if not model_name:
            return "unknown_model"

        # Normalize model name
        normalized_model_name = model_name.strip()

        if model_params:
            # Include only non-default params, ensure consistent ordering
            filtered_params = {}
            for k, v in model_params.items():
                if v is not None and v != "":
                    # Normalize parameter values
                    if isinstance(v, float):
                        # Round floats to avoid precision issues
                        filtered_params[k] = round(v, 6)
                    else:
                        filtered_params[k] = v

            if filtered_params:
                # Sort params by key for consistent string
                params_str = json.dumps(filtered_params, sort_keys=True, separators=(',', ':'))
                player_key = f"{normalized_model_name}#{params_str}"
                logger.debug(f"Generated player key: {player_key}")
                return player_key

        logger.debug(f"Generated player key: {normalized_model_name}")
        return normalized_model_name

    def _format_leaderboard(self, player_stats: Dict[str, PlayerStats]) -> List[Dict[str, Any]]:
        """Format leaderboard data - improved version with deduplication."""
        # Use dict for deduplication, keyed by model_name+model_params
        unique_entries = {}

        for player_key, stats in player_stats.items():
            # Create unique identifier
            unique_key = self._get_player_key(stats.model_name, stats.model_params)

            if unique_key in unique_entries:
                # If exists, merge statistics
                logger.warning(f"Duplicate entry found: {unique_key}, merging data")
                existing = unique_entries[unique_key]

                # Merge statistics
                existing['games_as_player'] += stats.games_as_player
                existing['games_as_designer'] += stats.games_as_designer
                existing['total_games'] += stats.total_games
                existing['overall_wins'] += stats.overall_wins
                existing['wins_as_player'] += stats.wins_as_player
                existing['wins_as_designer'] += stats.wins_as_designer
                existing['total_cost'] += stats.total_cost
                existing['total_input_tokens'] += stats.total_input_tokens
                existing['total_output_tokens'] += stats.total_output_tokens

                existing['total_score_as_player'] += stats.total_score_as_player
                existing['theoretical_max_scientist_score'] += stats.theoretical_max_scientist_score
                existing['total_score_as_designer'] += stats.total_score_as_designer
                existing['total_revised_score_as_designer'] += stats.total_revised_score_as_designer

                # Recalculate averages and rates
                existing['avg_score_as_player'] = round(existing['total_score_as_player'] / existing['games_as_player'],
                                                        2) if existing['games_as_player'] else 0.0
                existing['avg_score_as_designer'] = round(
                    existing['total_score_as_designer'] / existing['games_as_designer'], 2) if existing[
                    'games_as_designer'] else 0.0
                existing['avg_revised_score_as_designer'] = round(
                    existing['total_revised_score_as_designer'] / existing['games_as_designer'], 2) if existing[
                    'games_as_designer'] else 0.0

                existing['win_rate_as_player'] = round((existing['wins_as_player'] / existing['games_as_player'] * 100),
                                                       1) if existing['games_as_player'] else 0.0
                existing['win_rate_as_designer'] = round(
                    (existing['wins_as_designer'] / existing['games_as_designer'] * 100), 1) if existing[
                    'games_as_designer'] else 0.0
                existing['overall_win_rate'] = round((existing['overall_wins'] / existing['total_games'] * 100), 1) if \
                existing['total_games'] else 0.0
                existing['cost_per_game'] = round(existing['total_cost'] / existing['total_games'], 4) if existing[
                    'total_games'] else 0.0

                # Use the higher ELO rating
                if stats.elo_rating > existing['elo_rating']:
                    existing['elo_rating'] = round(stats.elo_rating, 1)
                    existing['trueskill_rating'] = round(stats.conservative_trueskill_rating, 1)
                    existing['trueskill_mu'] = round(stats.trueskill_rating.mu, 2)
                    existing['trueskill_sigma'] = round(stats.trueskill_rating.sigma, 2)
            else:
                # Create new entry
                entry = {
                    'model_name': stats.model_name,
                    'model_params': stats.model_params,
                    'elo_rating': round(stats.elo_rating, 1),
                    'trueskill_rating': round(stats.conservative_trueskill_rating, 1),
                    'trueskill_mu': round(stats.trueskill_rating.mu, 2),
                    'trueskill_sigma': round(stats.trueskill_rating.sigma, 2),
                    'games_as_player': stats.games_as_player,
                    'games_as_designer': stats.games_as_designer,
                    'total_games': stats.total_games,
                    'avg_score_as_player': round(stats.avg_score_as_player, 2),
                    'avg_score_as_designer': round(stats.avg_score_as_designer, 2),
                    'win_rate_as_player': round(stats.win_rate_as_player, 1),
                    'win_rate_as_designer': round(stats.win_rate_as_designer, 1),
                    'overall_win_rate': round(stats.overall_win_rate, 1),
                    'overall_wins': stats.overall_wins,
                    'wins_as_player': stats.wins_as_player,
                    'wins_as_designer': stats.wins_as_designer,
                    'cost_per_game': round(stats.cost_per_game, 4),
                    'total_cost': round(stats.total_cost, 4),
                    'total_input_tokens': stats.total_input_tokens,
                    'total_output_tokens': stats.total_output_tokens,
                    'total_score_as_player': stats.total_score_as_player,
                    'theoretical_max_scientist_score': stats.theoretical_max_scientist_score,
                    'total_score_as_designer': stats.total_score_as_designer,
                    'total_revised_score_as_designer': stats.total_revised_score_as_designer,
                    'avg_revised_score_as_designer': round(stats.avg_revised_score_as_designer, 2)
                }
                unique_entries[unique_key] = entry

        # Convert to list and sort by ELO rating
        leaderboard = list(unique_entries.values())
        leaderboard.sort(key=lambda x: x['elo_rating'], reverse=True)

        logger.info(f"Formatting complete, leaderboard entries: {len(leaderboard)} (deduplicated)")
        for i, entry in enumerate(leaderboard[:5]):  # Print top 5
            logger.info(
                f"  {i + 1}. {entry['model_name']}: ELO {entry['elo_rating']}, TrueSkill {entry['trueskill_rating']}, player WR {entry['win_rate_as_player']}%, overall WR {entry['overall_win_rate']}%, cost/game ${entry['cost_per_game']:.4f}")

        return leaderboard

    async def _save_leaderboard_to_db(self, leaderboard_data: List[Dict[str, Any]], test_set_id: Optional[str] = None):
        """Save leaderboard data to database - using separate columns, ensuring uniqueness."""
        try:
            if not leaderboard_data:
                logger.warning("No leaderboard data to save")
                return

            async with self.db_pool.acquire() as conn:
                # Begin transaction
                async with conn.transaction():
                    # First delete existing leaderboard data for this test set
                    if test_set_id:
                        deleted_count = await conn.fetchval("SELECT COUNT(*) FROM leaderboards WHERE test_set_id = $1",
                                                            test_set_id)
                        await conn.execute("DELETE FROM leaderboards WHERE test_set_id = $1", test_set_id)
                    else:
                        deleted_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM leaderboards WHERE test_set_id IS NULL")
                        await conn.execute("DELETE FROM leaderboards WHERE test_set_id IS NULL")

                    logger.info(f"Deleted {deleted_count} old leaderboard records")

                    # Insert new leaderboard data
                    inserted_count = 0
                    for rank, entry in enumerate(leaderboard_data, 1):
                        leaderboard_id = str(uuid.uuid4())

                        # Serialize model_params to JSON
                        model_params_json = None
                        if entry['model_params']:
                            model_params_json = json.dumps(entry['model_params'], sort_keys=True)

                        try:
                            await conn.execute(
                                """
                                INSERT INTO leaderboards (id, test_set_id, rank_position, model_name, model_params,
                                                          elo_rating, trueskill_rating, trueskill_mu, trueskill_sigma,
                                                          games_as_player, games_as_designer, total_games,
                                                          avg_score_as_player, avg_score_as_designer,
                                                          win_rate_as_player, win_rate_as_designer,
                                                          overall_win_rate,
                                                          wins_as_player, wins_as_designer,
                                                          overall_wins,
                                                          cost_per_game, total_cost,
                                                          total_input_tokens, total_output_tokens,
                                                          total_score_as_player, theoretical_max_scientist_score,
                                                          total_score_as_designer,
                                                          avg_revised_score_as_designer,
                                                          total_revised_score_as_designer,
                                                          created_at)
                                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                                        $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30)
                                """,
                                leaderboard_id, test_set_id, rank, entry['model_name'], model_params_json,
                                entry['elo_rating'], entry['trueskill_rating'], entry['trueskill_mu'],
                                entry['trueskill_sigma'],
                                entry['games_as_player'], entry['games_as_designer'], entry['total_games'],
                                entry['avg_score_as_player'], entry['avg_score_as_designer'],
                                entry['win_rate_as_player'], entry['win_rate_as_designer'],
                                entry['overall_win_rate'], entry['wins_as_player'], entry['wins_as_designer'],
                                entry['overall_wins'],
                                entry['cost_per_game'], entry['total_cost'],
                                entry['total_input_tokens'], entry['total_output_tokens'],
                                entry.get('total_score_as_player', 0),
                                entry.get('theoretical_max_scientist_score', 0),
                                entry.get('total_score_as_designer', 0),
                                entry.get('avg_revised_score_as_designer', 0.0),
                                entry.get('total_revised_score_as_designer', 0.0),
                                datetime.now()
                            )
                            inserted_count += 1
                        except Exception as e:
                            logger.error(f"Failed to insert leaderboard entry {entry['model_name']}: {e}")
                            continue

                    logger.info(f"Saved {inserted_count} new leaderboard records, test_set_id: {test_set_id or 'global'}")

        except Exception as e:
            logger.error(f"Failed to save leaderboard data: {e}", exc_info=True)

    async def get_cached_leaderboard(self, test_set_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Get cached leaderboard data from database - reading from separate columns."""
        try:
            async with self.db_pool.acquire() as conn:
                if test_set_id:
                    rows = await conn.fetch(
                        """
                        SELECT *
                        FROM leaderboards
                        WHERE test_set_id = $1
                        ORDER BY rank_position ASC
                        """,
                        test_set_id
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT *
                        FROM leaderboards
                        WHERE test_set_id IS NULL
                        ORDER BY rank_position ASC
                        """
                    )

                if rows:
                    leaderboard_data = []
                    seen_models = set()  # For detecting duplicates

                    for row in rows:
                        logger.info(
                            f"[DEBUG] DB row: model={row['model_name']}, wins_as_player={row['wins_as_player']}, wins_as_designer={row['wins_as_designer']}, games_as_player={row['games_as_player']}, games_as_designer={row['games_as_designer']}")

                        # Parse model_params
                        model_params = None
                        if row['model_params']:
                            try:
                                model_params = json.loads(row['model_params'])
                            except Exception as e:
                                logger.warning(f"Failed to parse model_params: {e}")

                        # Create unique identifier
                        unique_key = self._get_player_key(row['model_name'], model_params)

                        if unique_key in seen_models:
                            logger.warning(f"Skipping duplicate leaderboard entry: {unique_key}")
                            continue

                        seen_models.add(unique_key)

                        entry = {
                            'model_name': row['model_name'],
                            'model_params': model_params,
                            'elo_rating': float(row['elo_rating']),
                            'trueskill_rating': float(row['trueskill_rating']),
                            'trueskill_mu': float(row['trueskill_mu']),
                            'trueskill_sigma': float(row['trueskill_sigma']),
                            'games_as_player': row['games_as_player'],
                            'games_as_designer': row['games_as_designer'],
                            'total_games': row['total_games'],
                            'avg_score_as_player': float(row['avg_score_as_player']),
                            'avg_score_as_designer': float(row['avg_score_as_designer']),
                            'win_rate_as_player': float(row['win_rate_as_player']),
                            'win_rate_as_designer': float(row['win_rate_as_designer']),
                            'overall_win_rate': float(row['overall_win_rate']),
                            'wins_as_player': row['wins_as_player'],
                            'wins_as_designer': row['wins_as_designer'],
                            'overall_wins': row['overall_wins'],
                            'cost_per_game': float(row['cost_per_game']),
                            'total_cost': float(row['total_cost']),
                            'total_input_tokens': row['total_input_tokens'],
                            'total_output_tokens': row['total_output_tokens'],
                            'total_score_as_player': row.get('total_score_as_player', 0) or 0,
                            'theoretical_max_scientist_score': row.get('theoretical_max_scientist_score', 0) or 0,
                            'total_score_as_designer': row.get('total_score_as_designer', 0) or 0,
                            'avg_revised_score_as_designer': float(row.get('avg_revised_score_as_designer', 0) or 0),
                            'total_revised_score_as_designer': float(row.get('total_revised_score_as_designer', 0) or 0)
                        }

                        logger.info(
                            f"[DEBUG] Return entry: model={entry['model_name']}, wins_as_player={entry['wins_as_player']}, wins_as_designer={entry['wins_as_designer']}")

                        leaderboard_data.append(entry)

                    created_at = rows[0]['created_at'] if rows else datetime.now()
                    logger.info(f"Got cached leaderboard data, created at {created_at}, {len(leaderboard_data)} records")
                    return leaderboard_data

                logger.info(f"No cached leaderboard data found, test_set_id: {test_set_id or 'global'}")
                return None

        except Exception as e:
            logger.error(f"Failed to get cached leaderboard: {e}", exc_info=True)
            return None

    async def _fetch_designer_wins_from_analytics(self) -> Dict[str, Dict[str, Any]]:
        """Fetch designer win data from game_analytics table."""
        try:
            async with self.db_pool.acquire() as conn:
                query = """
                        SELECT game_id, rank_in_game_incl_designer
                        FROM game_analytics
                        WHERE rank_in_game_incl_designer IS NOT NULL \
                        """
                rows = await conn.fetch(query)

                result = {}
                for row in rows:
                    result[str(row['game_id'])] = {
                        'rank_in_game_incl_designer': row['rank_in_game_incl_designer']
                    }

                logger.info(f"Fetched {len(result)} designer ranking records from game_analytics")
                return result

        except Exception as e:
            logger.error(f"Failed to fetch designer win data: {e}", exc_info=True)
            return {}


async def create_leaderboard_tables(db_pool):
    """Create leaderboard-related database tables."""
    try:
        async with db_pool.acquire() as conn:
            # Legacy tables (games, game_players) are archived — no ALTER needed

            # Create test_sets table
            await conn.execute("""
                               CREATE TABLE IF NOT EXISTS test_sets
                               (
                                   id              TEXT PRIMARY KEY,
                                   name            TEXT  NOT NULL,
                                   description     TEXT,
                                   config          JSONB NOT NULL,
                                   created_at      TIMESTAMPTZ DEFAULT NOW(),
                                   status          TEXT        DEFAULT 'created',
                                   total_games     INTEGER     DEFAULT 0,
                                   completed_games INTEGER     DEFAULT 0,
                                   join_token      TEXT,
                                   player_sessions JSONB       DEFAULT '[]'::jsonb
                               )
                               """)

            # Drop old leaderboard table if exists
            await conn.execute("DROP TABLE IF EXISTS leaderboards")

            # Create new leaderboard table with separate columns and unique constraint
            await conn.execute("""
                               CREATE TABLE leaderboards
                               (
                                   id                         TEXT PRIMARY KEY,
                                   test_set_id                TEXT,
                                   rank_position              INTEGER        NOT NULL,
                                   model_name                 TEXT           NOT NULL,
                                   model_params               JSONB,
                                   elo_rating                 DECIMAL(10, 2) NOT NULL,
                                   trueskill_rating           DECIMAL(10, 2) NOT NULL,
                                   trueskill_mu               DECIMAL(10, 2) NOT NULL,
                                   trueskill_sigma            DECIMAL(10, 2) NOT NULL,
                                   games_as_player            INTEGER        NOT NULL DEFAULT 0,
                                   games_as_designer          INTEGER        NOT NULL DEFAULT 0,
                                   total_games                INTEGER        NOT NULL DEFAULT 0,
                                   avg_score_as_player        DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   avg_score_as_designer      DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   win_rate_as_player         DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   win_rate_as_designer       DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   overall_win_rate           DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   wins_as_player             INTEGER        NOT NULL DEFAULT 0,
                                   wins_as_designer           INTEGER        NOT NULL DEFAULT 0,
                                   overall_wins               INTEGER        NOT NULL DEFAULT 0,
                                   cost_per_game              DECIMAL(10, 6) NOT NULL DEFAULT 0.0,
                                   total_cost                 DECIMAL(10, 6) NOT NULL DEFAULT 0.0,
                                   total_input_tokens         INTEGER        NOT NULL DEFAULT 0,
                                   total_output_tokens        INTEGER        NOT NULL DEFAULT 0,
                                   total_score_as_player      INTEGER        NOT NULL DEFAULT 0,
                                   theoretical_max_scientist_score INTEGER    NOT NULL DEFAULT 0,
                                   total_score_as_designer    DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   avg_revised_score_as_designer   DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   total_revised_score_as_designer DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   created_at                 TIMESTAMPTZ             DEFAULT NOW(),
                                   FOREIGN KEY (test_set_id) REFERENCES test_sets (id) ON DELETE CASCADE,
                                   UNIQUE (test_set_id, model_name, model_params)
                               )
                               """)

            await conn.execute("CREATE INDEX IF NOT EXISTS idx_leaderboards_test_set_id ON leaderboards(test_set_id)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_leaderboards_rank ON leaderboards(test_set_id, rank_position)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_leaderboards_elo ON leaderboards(elo_rating DESC)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_leaderboards_unique_model ON leaderboards(test_set_id, model_name, model_params)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_created_at ON test_sets(created_at DESC)")

            logger.info("Leaderboard tables created successfully")

    except Exception as e:
        logger.error(f"Failed to create leaderboard tables: {e}")
