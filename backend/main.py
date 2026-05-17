import os
import uuid
import json
import logging
import asyncio
import string
import random
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Dict, Any, Set, Tuple
import asyncpg
from contextlib import asynccontextmanager
import httpx
from dotenv import load_dotenv
import threading
from collections import defaultdict

from prompt_manager import prompt_manager
from llm_client import LLMClient
from scoring import (
    calculate_score, calculate_ranks, analyze_action_log,
    calculate_designer_score,
)

# Import leaderboard module
try:
    from leaderboard import LeaderboardCalculator, create_leaderboard_tables
except ImportError:
    # If leaderboard module doesn't exist, create placeholder
    class LeaderboardCalculator:
        def __init__(self, db_pool):
            self.db_pool = db_pool

        async def calculate_leaderboard(self, test_set_id=None):
            return []

        async def get_cached_leaderboard(self, test_set_id=None):
            return None

import re


def strip_markdown_json(text: str) -> str:
    """Strip markdown code block wrappers (```json ... ```) from LLM responses."""
    stripped = text.strip()
    match = re.match(r'^```(?:json)?\s*\n?(.*?)\n?\s*```$', stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def try_fix_truncated_json(text: str) -> Optional[dict]:
    """Try to fix JSON that was truncated mid-output (e.g. due to token limit).

    Common pattern: the 'reasoning' field is cut off mid-string, causing
    'Unterminated string' JSON parse error. This function closes open strings
    and brackets to recover the valid data.
    """
    text = strip_markdown_json(text).strip()
    if not text:
        return None

    # Try parsing as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Track string/bracket state
    in_string = False
    escape_next = False
    stack = []  # track [ and {

    for char in text:
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
        if not in_string:
            if char in '{[':
                stack.append(char)
            elif char in '}]':
                if stack:
                    stack.pop()

    # Build closing sequence
    closing = '"' if in_string else ''
    for bracket in reversed(stack):
        closing += '}' if bracket == '{' else ']'

    if not closing:
        return None

    try:
        return json.loads(text + closing)
    except json.JSONDecodeError:
        return None


# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# All model traffic is routed through OpenRouter — one HTTP endpoint covers
# every provider used by the benchmark (OpenAI, Anthropic, Google, Meta, etc.).
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

openrouter_client: Optional[httpx.AsyncClient] = None
if OPENROUTER_API_KEY:
    try:
        openrouter_client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        logger.info("OpenRouter client initialized")
    except Exception as e:
        logger.error(f"OpenRouter client init failed: {e}")
        openrouter_client = None
else:
    logger.warning("OPENROUTER_API_KEY not set — all model calls will fail")

llm_client: Optional[LLMClient] = (
    LLMClient(openrouter_client=openrouter_client) if openrouter_client else None
)

# Global chat history storage - thread safe
chat_histories_lock = threading.Lock()
chat_histories: Dict[str, List[Dict[str, str]]] = defaultdict(list)

# Global background tasks manager
background_tasks_manager = None

# Global game state storage - for live viewing
game_states_lock = threading.Lock()
current_game_states: Dict[str, Dict[str, Any]] = {}

# Global token usage tracking - tracks each player's token usage
player_token_usage_lock = threading.Lock()
player_token_usage: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0})

# Human player: no event synchronization needed.
# Human players act independently via /human-action endpoint at their own pace.
# The game loop just polls until all players (LLM + Human) are finished.


class BackgroundTasksManager:
    """Background tasks manager."""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.task_lock = asyncio.Lock()

    async def start_test_set_execution(self, test_set_id: str):
        """Start test set background execution."""
        async with self.task_lock:
            if test_set_id in self.running_tasks:
                logger.info(f"Test set {test_set_id} is already running")
                return

            # Check test set status, update from pending to created if needed
            async with self.db_pool.acquire() as conn:
                test_set_row = await conn.fetchrow("SELECT status FROM test_sets WHERE id = $1", test_set_id)
                if test_set_row and test_set_row['status'] == 'pending':
                    await conn.execute(
                        "UPDATE test_sets SET status = 'created' WHERE id = $1",
                        test_set_id
                    )
                    logger.info(f"Updated test set {test_set_id} status from pending to created")

            # Create background task
            task = asyncio.create_task(self._execute_test_set(test_set_id))
            self.running_tasks[test_set_id] = task
            logger.info(f"Started background execution for test set {test_set_id}")

    async def _execute_test_set(self, test_set_id: str):
        """Core logic for executing a test set."""
        try:
            logger.info(f"Starting test set execution: {test_set_id}")

            # Get test set configuration
            async with self.db_pool.acquire() as conn:
                test_set_row = await conn.fetchrow("SELECT * FROM test_sets WHERE id = $1", test_set_id)
                if not test_set_row:
                    logger.error(f"Test set {test_set_id} not found")
                    return

                config = json.loads(test_set_row['config'])

                # Update status to running
                await conn.execute(
                    "UPDATE test_sets SET status = 'running' WHERE id = $1",
                    test_set_id
                )

            # Generate game configurations
            game_configs = self._generate_game_configs(config)
            total_games = len(game_configs)

            logger.info(f"Test set {test_set_id}: {total_games} total games")

            # Split game configs into LLM-only and per-human tracks
            human_participants = [
                (i, p) for i, p in enumerate(config.get('participants', []))
                if p.get('participant_type') == 'Human'
            ]

            if human_participants:
                # Create separate tracks: LLM-only games + per-human game series
                llm_game_configs = []
                # human_game_tracks: {participant_index: [game_configs]}
                human_game_tracks: Dict[int, List[Dict[str, Any]]] = {idx: [] for idx, _ in human_participants}

                for gc in game_configs:
                    # LLM-only variant: remove human players
                    llm_players = [p for p in gc['players'] if p['type'] != 'Human']
                    if llm_players:
                        llm_gc = {**gc, 'players': llm_players}
                        llm_game_configs.append(llm_gc)

                    # Per-human variant: one game per human player (solo)
                    for p_idx, p_config in human_participants:
                        human_players = [p for p in gc['players'] if p['type'] == 'Human' and p.get('_participant_index') == p_idx]
                        if human_players:
                            human_gc = {**gc, 'players': human_players}
                            human_game_tracks[p_idx].append(human_gc)

                # Count total: LLM games + human games (each human plays all patterns)
                total_llm_games = len(llm_game_configs)
                total_human_games = sum(len(track) for track in human_game_tracks.values())
                actual_total = total_llm_games + total_human_games

                total_games = actual_total  # Update local variable for final completion mark
                logger.info(f"Test set {test_set_id}: split into {total_llm_games} LLM games + {total_human_games} human games ({len(human_participants)} humans)")

                # Update total_games in DB
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE test_sets SET total_games = $1 WHERE id = $2",
                        actual_total, test_set_id
                    )

                # Shared progress counter
                completed_count = 0
                completed_lock = asyncio.Lock()

                async def increment_progress():
                    nonlocal completed_count
                    async with completed_lock:
                        completed_count += 1
                        async with self.db_pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE test_sets SET completed_games = $1 WHERE id = $2",
                                completed_count, test_set_id
                            )

                # Run LLM games
                llm_task = None
                if llm_game_configs:
                    async def run_llm_games():
                        for gi, gc in enumerate(llm_game_configs):
                            try:
                                mp = await self._generate_master_pattern_via_api(gc)
                                result = await self._execute_single_game_with_states(
                                    test_set_id, gi, gc, mp)
                                await self._save_game_result_via_api(test_set_id, gc, mp, result)
                                await increment_progress()
                            except Exception as e:
                                logger.error(f"LLM game {gi+1} failed: {e}")
                                await increment_progress()
                    llm_task = asyncio.create_task(run_llm_games())

                # Pre-generate shared game IDs and master patterns per game index
                # so all humans playing the same game index are grouped together
                num_games_per_human = max(len(track) for track in human_game_tracks.values()) if human_game_tracks else 0
                shared_game_ids = [str(uuid.uuid4()) for _ in range(num_games_per_human)]
                shared_master_patterns: Dict[int, List[List[str]]] = {}

                # Generate master patterns upfront (use the first human's game config as reference)
                first_track = next(iter(human_game_tracks.values()), [])
                for gi, gc in enumerate(first_track):
                    try:
                        shared_master_patterns[gi] = await self._generate_master_pattern_via_api(gc)
                    except Exception as e:
                        logger.error(f"Failed to generate master pattern for game {gi}: {e}")
                        # Fallback: will be generated per-player if missing

                # Run each human's games in a separate task
                human_tasks = []
                for p_idx, p_config in human_participants:
                    track = human_game_tracks[p_idx]
                    if not track:
                        continue
                    state_key = f"{test_set_id}:human:{p_idx}"

                    async def run_human_games(sk=state_key, games=track, pidx=p_idx):
                        for gi, gc in enumerate(games):
                            try:
                                mp = shared_master_patterns.get(gi) or await self._generate_master_pattern_via_api(gc)
                                result = await self._execute_single_game_with_states(
                                    sk, gi, gc, mp)
                                game_id = shared_game_ids[gi] if gi < len(shared_game_ids) else None
                                await self._save_game_result_via_api(test_set_id, gc, mp, result, shared_game_id=game_id)
                                await increment_progress()

                                # Inter-game ready check: wait for player to signal ready
                                if gi < len(games) - 1:  # Not the last game
                                    # Check if frontend already signaled ready (race-safe)
                                    with game_states_lock:
                                        state = current_game_states.get(sk, {})
                                        already_ready = state.get('ready_for_next', False)
                                    if not already_ready:
                                        # Poll-wait until ready or 5-minute timeout
                                        timeout_seconds = 300
                                        waited = 0
                                        while waited < timeout_seconds:
                                            with game_states_lock:
                                                state = current_game_states.get(sk, {})
                                                if state.get('ready_for_next', False):
                                                    break
                                            await asyncio.sleep(1)
                                            waited += 1
                                        if waited >= timeout_seconds:
                                            logger.info(f"Human participant {pidx} auto-advanced after 5min timeout")
                                    # Reset for next inter-game wait
                                    with game_states_lock:
                                        if sk in current_game_states:
                                            current_game_states[sk]['ready_for_next'] = False

                            except Exception as e:
                                logger.error(f"Human game (participant {pidx}) {gi+1} failed: {e}")
                                await increment_progress()
                        # Clean up human state
                        with game_states_lock:
                            if sk in current_game_states:
                                del current_game_states[sk]

                    human_tasks.append(asyncio.create_task(run_human_games()))

                # Wait for all tracks to complete
                all_tasks = ([llm_task] if llm_task else []) + human_tasks
                await asyncio.gather(*all_tasks, return_exceptions=True)

            else:
                # No human participants — existing logic
                # Per-participant evolution memory (keyed by llmModel)
                evolution_memory: Dict[str, List[Dict]] = defaultdict(list)
                has_any_evolving = False
                for participant in config.get('participants', []):
                    evolving_cfg = participant.get('evolving_config')
                    if evolving_cfg and evolving_cfg.get('enabled'):
                        has_any_evolving = True
                        if evolving_cfg.get('mode') == 'imported' and evolving_cfg.get('import_game_ids'):
                            imported_history = await self._fetch_imported_game_history(
                                evolving_cfg['import_game_ids'], participant['model_name']
                            )
                            evolution_memory[participant['model_name']] = imported_history

                if not has_any_evolving:
                    evolution_memory = None

                if has_any_evolving:
                    await self._execute_games_sequential(
                        test_set_id, game_configs, total_games, evolution_memory
                    )
                else:
                    await self._execute_games_concurrent(
                        test_set_id, game_configs, total_games
                    )

            # Mark as completed
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE test_sets SET status = 'completed', completed_games = $1 WHERE id = $2",
                    total_games, test_set_id
                )

            logger.info(f"Test set {test_set_id} execution completed")

            # Check for next pending test set
            await self._check_and_start_next_test_set()

        except Exception as e:
            logger.error(f"Test set {test_set_id} execution failed: {e}")
            # Mark as failed
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE test_sets SET status = 'failed' WHERE id = $1",
                    test_set_id
                )
        finally:
            # Clean up task
            async with self.task_lock:
                if test_set_id in self.running_tasks:
                    del self.running_tasks[test_set_id]

            # Clean up game state (main + all human state keys)
            with game_states_lock:
                if test_set_id in current_game_states:
                    del current_game_states[test_set_id]
                human_keys = [k for k in current_game_states if k.startswith(f"{test_set_id}:human:")]
                for k in human_keys:
                    del current_game_states[k]

    async def _execute_games_sequential(self, test_set_id: str, game_configs: list,
                                         total_games: int, evolution_memory: Dict[str, List[Dict]]):
        """Execute games sequentially (required when evolving is enabled)."""
        for game_index, game_config in enumerate(game_configs):
            try:
                logger.info(f"Executing game {game_index + 1}/{total_games} (sequential)")

                master_pattern = await self._generate_master_pattern_via_api(game_config)

                game_result = await self._execute_single_game_with_states(
                    test_set_id, game_index, game_config, master_pattern,
                    evolution_memory=evolution_memory
                )

                # Update evolution memory per-participant after each game
                if evolution_memory is not None:
                    for player in game_config['players']:
                        player_evolving = player.get('evolving_config')
                        if player_evolving and player_evolving.get('enabled'):
                            should_accumulate = (
                                player_evolving.get('mode') == 'fresh' or
                                player_evolving.get('accumulate', True)
                            )
                            if should_accumulate:
                                player_key = player.get('llmModel', player['name'])
                                player_state = game_result['playerStates'].get(player['id'], {})
                                evolution_memory[player_key].append({
                                    'grid_size': game_config['baseSettings']['gridSize'],
                                    'num_symbols': game_config['baseSettings']['numSymbols'],
                                    'score': player_state.get('score', 0),
                                    'num_observations': len(player_state.get('queriedCells', [])),
                                    'action_log': player_state.get('log', []),
                                    'final_guess': player_state.get('finalGuess'),
                                    'queried_cells': player_state.get('queriedCells', []),
                                    'key_insight': player_state['log'][-1] if player_state.get('log') else None,
                                })

                await self._save_game_result_via_api(test_set_id, game_config, master_pattern, game_result)

                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE test_sets SET completed_games = $1 WHERE id = $2",
                        game_index + 1, test_set_id
                    )
                logger.info(f"Completed {game_index + 1}/{total_games} games")

                with game_states_lock:
                    if test_set_id in current_game_states:
                        del current_game_states[test_set_id]


            except Exception as e:
                logger.error(f"Game {game_index + 1} execution failed: {e}")
                # Still increment progress so we don't get stuck
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE test_sets SET completed_games = $1 WHERE id = $2",
                        game_index + 1, test_set_id
                    )
                continue

    async def _execute_games_concurrent(self, test_set_id: str, game_configs: list, total_games: int):
        """Execute games concurrently when no evolving dependencies exist."""
        semaphore = asyncio.Semaphore(10)  # Max 10 concurrent games
        completed_count = 0
        completed_lock = asyncio.Lock()

        async def run_single_game(game_index: int, game_config: Dict[str, Any]):
            nonlocal completed_count
            async with semaphore:
                try:
                    logger.info(f"Executing game {game_index + 1}/{total_games} (concurrent)")

                    master_pattern = await self._generate_master_pattern_via_api(game_config)

                    game_result = await self._execute_single_game_with_states(
                        test_set_id, game_index, game_config, master_pattern
                    )

                    await self._save_game_result_via_api(test_set_id, game_config, master_pattern, game_result)

                    async with completed_lock:
                        completed_count += 1
                        async with self.db_pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE test_sets SET completed_games = $1 WHERE id = $2",
                                completed_count, test_set_id
                            )
                        logger.info(f"Completed {completed_count}/{total_games} games")

                except Exception as e:
                    logger.error(f"Game {game_index + 1} execution failed: {e}")
                    async with completed_lock:
                        completed_count += 1
                        async with self.db_pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE test_sets SET completed_games = $1 WHERE id = $2",
                                completed_count, test_set_id
                            )

        # Launch all games concurrently (semaphore limits actual concurrency)
        tasks = [run_single_game(i, gc) for i, gc in enumerate(game_configs)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Clean up game states
        with game_states_lock:
            if test_set_id in current_game_states:
                del current_game_states[test_set_id]

    async def _fetch_imported_game_history(self, game_ids: List[str], model_name: str) -> List[Dict]:
        """Fetch comprehensive game history from database for evolving import mode."""
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT ga.grid_size, ga.num_symbols, ga.final_score, ga.observation_count,
                           ga.action_log, ga.final_guess, ga.queried_cells, ga.master_pattern
                    FROM game_analytics ga
                    WHERE ga.game_id::text = ANY($1)
                      AND ga.participant_llm_model = $2
                    ORDER BY ga.created_at ASC
                    """,
                    game_ids, model_name
                )

                history = []
                for row in rows:
                    action_log = json.loads(row['action_log']) if row['action_log'] else []
                    entry = {
                        'grid_size': row['grid_size'],
                        'num_symbols': row['num_symbols'],
                        'score': row['final_score'],
                        'num_observations': row['observation_count'] or 0,
                        'action_log': action_log,
                        'final_guess': json.loads(row['final_guess']) if row['final_guess'] else None,
                        'queried_cells': json.loads(row['queried_cells']) if row['queried_cells'] else [],
                        'master_pattern': json.loads(row['master_pattern']) if row['master_pattern'] else None,
                        'key_insight': action_log[-1] if action_log else None,
                    }
                    history.append(entry)

                return history
        except Exception as e:
            logger.error(f"Failed to fetch imported game history: {e}")
            return []

    async def _check_and_start_next_test_set(self):
        """Check and start the next pending test set."""
        try:
            async with self.db_pool.acquire() as conn:
                next_test_set = await conn.fetchrow(
                    "SELECT id FROM test_sets WHERE status IN ('created', 'pending') ORDER BY created_at ASC LIMIT 1"
                )

            if next_test_set:
                logger.info(f"Found next pending test set: {next_test_set['id']}")
                await self.start_test_set_execution(next_test_set['id'])
        except Exception as e:
            logger.error(f"Failed to check next test set: {e}")

    @staticmethod
    def _build_player_from_participant(index: int, p: Dict[str, Any], human_counter: Dict[str, int]) -> Dict[str, Any]:
        """Build a player config dict from a participant config."""
        participant_type = p.get('participant_type', 'LLM')
        if participant_type == 'Human':
            human_counter['count'] = human_counter.get('count', 0) + 1
            name = p.get('human_name', '').strip() or f"Human-{human_counter['count']}"
            return {
                'id': f'player-{index}',
                'name': name,
                'type': 'Human',
                '_participant_index': index,  # Track which participant this is
            }
        else:
            return {
                'id': f'player-{index}',
                'name': f"{p['model_name']}{'-custom' if p.get('model_params') else ''}",
                'type': 'LLM',
                'llmModel': p['model_name'],
                'llmModelParams': p.get('model_params'),
                'evolving_config': p.get('evolving_config'),
            }

    def _generate_game_configs(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate game configuration list."""
        game_configs = []

        for game_template in config['games']:
            for repeat in range(game_template['repeat_count']):
                if config['llm_rotate_designer']:
                    # Only LLM participants rotate as designer; humans are scientist-only
                    llm_participants = [p for p in config['participants'] if p.get('participant_type', 'LLM') == 'LLM']
                    for designer_index, designer in enumerate(llm_participants):
                        # Create player list from ALL participants
                        human_counter = {}
                        players_for_this_game = []
                        for i, p in enumerate(config['participants']):
                            players_for_this_game.append(
                                self._build_player_from_participant(i, p, human_counter)
                            )
                        game_config = {
                            'baseSettings': {
                                'gridSize': game_template['grid_size'],
                                'numSymbols': game_template['num_symbols'],
                            },
                            'designer': {
                                'type': 'LLM',
                                'llmModel': designer['model_name'],
                                'llmModelParams': designer.get('model_params'),
                                'llmPrompt': game_template.get('optional_prompt'),
                                'evolving_config': designer.get('evolving_config'),
                                'patternMode': game_template.get('pattern_mode', 'LLM_Designer'),
                            },
                            'players': players_for_this_game,
                        }
                        game_configs.append(game_config)
                else:
                    # Check if there's an LLM-designed pattern
                    pattern_mode = game_template.get('pattern_mode', 'Random')
                    designer_config = {
                        'type': 'Human',
                        'patternMode': pattern_mode,
                    }

                    # If LLM mode with a designed pattern, use it as custom pattern
                    if pattern_mode == 'LLM' and game_template.get('llm_designed_pattern'):
                        designer_config['patternMode'] = 'Custom'
                        designer_config['customPattern'] = game_template['llm_designed_pattern']
                    elif pattern_mode == 'LLM_Designer':
                        # LLM Designer mode: pattern generated fresh per game at runtime
                        designer_config = {
                            'type': 'LLM',
                            'llmModel': game_template.get('llm_pattern_model'),
                            'llmModelParams': game_template.get('llm_pattern_model_params'),
                            'llmPrompt': game_template.get('llm_pattern_prompt') or game_template.get('optional_prompt'),
                            'patternMode': 'LLM_Designer',
                        }
                    elif pattern_mode == 'Custom' and game_template.get('custom_pattern'):
                        designer_config['customPattern'] = game_template['custom_pattern']
                    elif pattern_mode == 'Visual':
                        designer_config['symmetryType'] = game_template.get('symmetry_type', 'Left-Right')
                    elif pattern_mode == 'Algorithmic':
                        designer_config['shiftStep'] = game_template.get('shift_step', 1)

                    human_counter = {}
                    game_config = {
                        'baseSettings': {
                            'gridSize': game_template['grid_size'],
                            'numSymbols': game_template['num_symbols'],
                        },
                        'designer': designer_config,
                        'players': [
                            self._build_player_from_participant(i, p, human_counter)
                            for i, p in enumerate(config['participants'])
                        ],
                    }
                    game_configs.append(game_config)

        return game_configs

    async def _generate_master_pattern_via_api(self, game_config: Dict[str, Any]) -> List[List[str]]:
        """Generate master pattern using the design pattern API."""

        # First check for custom pattern
        if (game_config['designer']['type'] == 'Human' and
                game_config['designer'].get('patternMode') == 'Custom' and
                game_config['designer'].get('customPattern')):

            custom_pattern = game_config['designer']['customPattern']
            logger.info(f"Using custom pattern: {custom_pattern}")

            # Validate custom pattern format and size
            grid_size = game_config['baseSettings']['gridSize']
            if (isinstance(custom_pattern, list) and
                    len(custom_pattern) == grid_size and
                    all(isinstance(row, list) and len(row) == grid_size for row in custom_pattern)):

                logger.info("Custom pattern validated, using provided pattern")
                return custom_pattern
            else:
                logger.warning("Invalid custom pattern format, falling back to random")

        # LLM designer mode
        if game_config['designer']['type'] == 'LLM':
            try:
                # Build DesignPatternRequest
                request_data = {
                    'gridSize': game_config['baseSettings']['gridSize'],
                    'numSymbols': game_config['baseSettings']['numSymbols'],
                    'llmModel': game_config['designer']['llmModel'],
                    'llmModelParams': game_config['designer'].get('llmModelParams'),
                    'prompt': game_config['designer'].get('llmPrompt')
                }

                # Call design pattern logic with retry
                design_request = DesignPatternRequest(**request_data)
                design_response = await design_pattern_logic_with_retry(design_request)
                logger.info("LLM designer pattern generated successfully")

                # Store designer's pattern description in game_config for persistence
                if design_response.description:
                    game_config['designer']['description'] = design_response.description

                return design_response.pattern

            except Exception as e:
                logger.error(f"LLM pattern generation failed: {e}")

        # Fall back to random pattern
        logger.info("Using random pattern generation")
        grid_size = game_config['baseSettings']['gridSize']
        num_symbols = game_config['baseSettings']['numSymbols']
        symbols = ALL_SYMBOLS_PY[:num_symbols]

        import random
        pattern = []
        for _ in range(grid_size):
            row = [random.choice(symbols) for _ in range(grid_size)]
            pattern.append(row)

        return pattern

    async def _execute_player_turn(self, game_id: str, player: Dict, player_state: Dict,
                                    game_config: Dict[str, Any], master_pattern: List[List[str]],
                                    symbols_in_use: list):
        """Execute a single player's turn. Modifies player_state in-place."""
        if player['type'] == 'Human':
            # Human turns are handled externally via /human-action endpoint
            player_state['isWaitingForLLM'] = False
            return

        grid_size = game_config['baseSettings']['gridSize']
        try:
            llm_request_data = {
                'playerId': player['id'],
                'playerName': player['name'],
                'gameId': game_id,
                'gridSize': grid_size,
                'symbolsInUse': symbols_in_use,
                'currentGrid': player_state['grid'],
                'llmModel': player.get('llmModel', 'openai/gpt-4o-mini'),
                'llmModelParams': player.get('llmModelParams'),
                'turnNumber': player_state['turnNumber']
            }

            llm_request = LLMPlayerTurnRequest(**llm_request_data)
            llm_response, actual_input_tokens, actual_output_tokens = await llm_player_turn_logic_with_retry(
                llm_request)

            player_state['isWaitingForLLM'] = False
            player_state['turnNumber'] += 1

            player_state['inputTokens'] += actual_input_tokens
            player_state['outputTokens'] += actual_output_tokens

            # Process LLM response
            if llm_response.action == 'observe':
                if llm_response.cellsToObserve:
                    observed_coords = []
                    newly_queried = []

                    for cell in llm_response.cellsToObserve[:3]:  # Max 3 cells
                        row, col = cell.row, cell.col
                        if (0 <= row < grid_size and 0 <= col < grid_size and
                                player_state['grid'][row][col] == '?'):
                            player_state['grid'][row][col] = master_pattern[row][col]
                            observed_coords.append(f"{chr(65 + col)}{row + 1}")
                            newly_queried.append({'row': row, 'col': col})

                    player_state['queriedCells'].extend(newly_queried)
                    if observed_coords:
                        player_state['log'].append(
                            f"Turn {player_state['turnNumber'] - 1}: Observed cells: {', '.join(observed_coords)}. {llm_response.reasoning}")
                    else:
                        player_state['log'].append(
                            f"Turn {player_state['turnNumber'] - 1}: No valid cells to observe. {llm_response.reasoning}")

            elif llm_response.action == 'guess':
                if llm_response.guessGrid and len(llm_response.guessGrid) == grid_size:
                    player_state['finalGuess'] = llm_response.guessGrid
                    player_state['isFinished'] = True

                    score = calculate_score(
                        master_pattern, llm_response.guessGrid, player_state['queriedCells'], grid_size
                    )
                    player_state['score'] = score

                    confidence_text = f" (Confidence: {llm_response.confidence * 100:.1f}%)" if llm_response.confidence else ""
                    player_state['log'].append(
                        f"Turn {player_state['turnNumber'] - 1}: Final guess submitted{confidence_text}. Score: {score}. {llm_response.reasoning}")

            else:  # give_up
                player_state['finalGuess'] = [['?' for _ in range(grid_size)] for _ in range(grid_size)]
                player_state['isFinished'] = True
                player_state['score'] = 0
                player_state['log'].append(
                    f"Turn {player_state['turnNumber'] - 1}: Gave up the game. Final score: 0. {llm_response.reasoning}")

        except Exception as e:
            logger.error(f"Player {player['name']} turn {player_state['turnNumber']} failed: {e}")
            player_state['isWaitingForLLM'] = False
            player_state['finalGuess'] = [['?' for _ in range(grid_size)] for _ in range(grid_size)]
            player_state['isFinished'] = True
            player_state['score'] = 0
            player_state['log'].append(
                f"Turn {player_state['turnNumber']}: Error occurred after maximum retries, game ended. {str(e)}")

    async def _execute_single_game_with_states(self, test_set_id: str, game_index: int,
                                               game_config: Dict[str, Any], master_pattern: List[List[str]],
                                               evolution_memory: Optional[Dict[str, List[Dict]]] = None) -> Dict[
        str, Any]:
        """Execute a single game with real-time state updates."""
        game_id = str(uuid.uuid4())

        # Initialize game state
        game_state = {
            'gameId': game_id,
            'gameIndex': game_index,
            'gameConfig': game_config,
            'masterPattern': master_pattern,
            'currentPhase': 'playing',
            'playerStates': {},
            'allPlayersFinished': False,
            'currentTurn': 1
        }

        # Initialize player states
        for player in game_config['players']:
            game_state['playerStates'][player['id']] = {
                'id': player['id'],
                'name': player['name'],
                'type': player['type'],
                'llmModel': player.get('llmModel'),
                'llmModelParams': player.get('llmModelParams'),
                'grid': [['?' for _ in range(game_config['baseSettings']['gridSize'])]
                         for _ in range(game_config['baseSettings']['gridSize'])],
                'queriedCells': [],
                'selectedCells': [],
                'isGuessing': False,
                'isGuessMode': False,
                'log': [f"Game started for {player['name']}."],
                'score': None,
                'isFinished': False,
                'finalGuess': None,
                'turnNumber': 1,
                'isWaitingForLLM': False,
                'isPaused': False,
                'inputTokens': 0,  # Token tracking
                'outputTokens': 0
            }

        # Store in global state
        with game_states_lock:
            current_game_states[test_set_id] = game_state

        # Execute game using LLM player API
        symbols_in_use = ALL_SYMBOLS_PY[:game_config['baseSettings']['numSymbols']]

        # Pre-initialize chat histories with evolving context (LLM players only)
        for player in game_config['players']:
            if player['type'] == 'Human':
                continue  # Human players don't need chat history
            evolving_context = None
            player_evolving = player.get('evolving_config')
            if player_evolving and player_evolving.get('enabled') and evolution_memory:
                player_key = player.get('llmModel', player['name'])
                if player_key in evolution_memory and evolution_memory[player_key]:
                    evolving_context = prompt_manager.get_evolving_context(evolution_memory[player_key])
            initialize_player_chat(
                game_id, player['id'], player['name'],
                game_config['baseSettings']['gridSize'], symbols_in_use,
                evolving_context=evolving_context
            )

        # Mark human players as waiting from the start (they play independently via the API)
        for player in game_config['players']:
            if player['type'] == 'Human':
                game_state['playerStates'][player['id']]['isWaitingForHuman'] = True
        with game_states_lock:
            current_game_states[test_set_id] = game_state

        turn = 0
        # Safety timeout: only triggers if human player abandons the game (e.g. closes browser and never returns).
        # In normal play this should never be reached.
        human_timeout_seconds = 7200  # 120 minutes
        human_start_time = asyncio.get_event_loop().time()

        while True:
            # Check if all players are finished
            all_finished = all(
                game_state['playerStates'][p['id']]['finalGuess'] is not None
                for p in game_config['players']
            )
            if all_finished:
                game_state['allPlayersFinished'] = True
                game_state['currentPhase'] = 'results'
                with game_states_lock:
                    current_game_states[test_set_id] = game_state
                break

            # Collect unfinished LLM players only (humans play independently)
            llm_unfinished = [
                (player, game_state['playerStates'][player['id']])
                for player in game_config['players']
                if player['type'] == 'LLM' and game_state['playerStates'][player['id']]['finalGuess'] is None
            ]

            if llm_unfinished:
                turn += 1
                game_state['currentTurn'] = turn

                # Mark LLM players as waiting
                for player, ps in llm_unfinished:
                    ps['isWaitingForLLM'] = True
                with game_states_lock:
                    current_game_states[test_set_id] = game_state

                # Execute LLM player turns concurrently
                tasks = [
                    self._execute_player_turn(
                        game_id, player, player_state, game_config, master_pattern, symbols_in_use
                    )
                    for player, player_state in llm_unfinished
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        player_name = llm_unfinished[i][0]['name']
                        logger.error(f"Unexpected error in concurrent player turn for {player_name}: {result}")

                # Update global state after LLM turns complete
                with game_states_lock:
                    current_game_states[test_set_id] = game_state
            else:
                # Only human players remain — poll until they finish or timeout
                elapsed = asyncio.get_event_loop().time() - human_start_time
                if elapsed > human_timeout_seconds:
                    # Auto give-up for humans who haven't finished
                    grid_size = game_config['baseSettings']['gridSize']
                    for player in game_config['players']:
                        ps = game_state['playerStates'][player['id']]
                        if player['type'] == 'Human' and not ps.get('isFinished'):
                            ps['finalGuess'] = [['?' for _ in range(grid_size)] for _ in range(grid_size)]
                            ps['isFinished'] = True
                            ps['score'] = 0
                            ps['isWaitingForHuman'] = False
                            ps['log'].append(
                                f"Turn {ps['turnNumber']}: Timed out waiting for human input. Game forfeited. Final score: 0.")
                            logger.warning(f"Human player {player['name']} timed out in game {game_id}")
                    with game_states_lock:
                        current_game_states[test_set_id] = game_state
                    continue  # Will break on next iteration's all_finished check

                await asyncio.sleep(1)  # Poll every second

        # Calculate final scores
        player_scores = []
        for player in game_config['players']:
            player_state = game_state['playerStates'][player['id']]
            if player_state['score'] is None:
                # If no score, calculate a default score
                score = calculate_score(
                    master_pattern,
                    player_state['finalGuess'] or [['?' for _ in range(game_config['baseSettings']['gridSize'])]
                                                   for _ in range(game_config['baseSettings']['gridSize'])],
                    player_state['queriedCells'],
                    game_config['baseSettings']['gridSize']
                )
                player_state['score'] = score

            player_scores.append({
                'playerId': player['id'],
                'score': player_state['score']
            })

        return {
            'playerStates': {pid: pstate for pid, pstate in game_state['playerStates'].items()},
            'playerScores': player_scores
        }

    async def _save_game_result_via_api(self, test_set_id: str, game_config: Dict[str, Any],
                                        master_pattern: List[List[str]], game_result: Dict[str, Any],
                                        shared_game_id: Optional[str] = None):
        """Save game results using the save game API.

        If shared_game_id is provided, all players will be saved under that game_id
        (used for grouping multiple human players into the same game).
        """
        try:
            # Build GameCreateRequest
            players_data = []
            for player in game_config['players']:
                player_state = game_result['playerStates'][player['id']]
                player_score = next((ps for ps in game_result['playerScores'] if ps['playerId'] == player['id']),
                                    {'score': 0})

                # Convert queried_cells format
                queried_cells = []
                for cell in player_state.get('queriedCells', []):
                    queried_cells.append(PositionModel(row=cell['row'], col=cell['col']))

                player_data = PlayerStateInGame(
                    player_name_in_game=player['name'],
                    player_type=player['type'],
                    player_llm_model=player.get('llmModel'),
                    player_llm_model_params=LLMModelParams(**player.get('llmModelParams', {})) if player.get(
                        'llmModelParams') else None,
                    final_score=player_score['score'],
                    final_guess=player_state.get('finalGuess'),
                    action_log=player_state.get('log'),
                    queried_cells=queried_cells,
                    input_tokens=player_state.get('inputTokens', 0),
                    output_tokens=player_state.get('outputTokens', 0)
                )
                players_data.append(player_data)

            game_request = GameCreateRequest(
                grid_size=game_config['baseSettings']['gridSize'],
                num_symbols=game_config['baseSettings']['numSymbols'],
                designer_type=game_config['designer']['type'],
                designer_llm_model=game_config['designer'].get('llmModel'),
                designer_llm_model_params=LLMModelParams(**game_config['designer'].get('llmModelParams', {})) if
                game_config['designer'].get('llmModelParams') else None,
                designer_pattern_mode=game_config['designer'].get('patternMode'),
                master_pattern=master_pattern,
                game_config_dump=game_config,
                players=players_data,
                test_set_id=test_set_id
            )

            # Call save game logic directly
            await save_game_logic(game_request, override_game_id=shared_game_id)

        except Exception as e:
            logger.error(f"Failed to save game results: {e}")


# Scoring functions imported from scoring.py:
# calculate_score, calculate_ranks, analyze_action_log,
# designer_percentile, designer_in_game_score, designer_meta_score,
# calculate_designer_scores


# --- Global exception handlers ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create database connection pool on startup
    global db_pool, background_tasks_manager
    try:
        DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/patterns_db")
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("Database connection pool created successfully")

        # Auto-create tables if they don't exist
        await create_tables_if_not_exist()

        # Create leaderboard tables
        await create_leaderboard_tables(db_pool)

        # Migrations: add join_token and player_sessions columns to test_sets
        async with db_pool.acquire() as conn:
            for col, col_def in [
                ('join_token', 'TEXT'),
                ('player_sessions', "JSONB DEFAULT '[]'::jsonb"),
            ]:
                col_exists = await conn.fetchval("""
                    SELECT EXISTS (SELECT FROM information_schema.columns
                                   WHERE table_name = 'test_sets' AND column_name = $1)
                """, col)
                if not col_exists:
                    logger.info(f"Adding column {col} to test_sets...")
                    await conn.execute(f"ALTER TABLE test_sets ADD COLUMN {col} {col_def}")

        # Initialize background tasks manager
        background_tasks_manager = BackgroundTasksManager(db_pool)

        # Check for pending test sets on startup
        await background_tasks_manager._check_and_start_next_test_set()

    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        db_pool = None

    yield

    # Clean up connection pool on shutdown
    if db_pool:
        await db_pool.close()
        logger.info("Database connection pool closed")

    # Close OpenRouter client
    if openrouter_client:
        await openrouter_client.aclose()
        logger.info("OpenRouter client closed")


async def create_tables_if_not_exist():
    """Create database tables if they don't exist."""
    if not db_pool:
        logger.warning("Database pool not initialized, skipping table creation")
        return

    try:
        async with db_pool.acquire() as conn:
            # ── Step 1: Ensure game_analytics table exists ──
            analytics_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'game_analytics')"
            )

            if not analytics_exists:
                logger.info("Creating game_analytics table...")
                await conn.execute("""
                                   CREATE TABLE game_analytics
                                   (
                                       id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                                       game_id                      UUID    NOT NULL,
                                       grid_size                    INTEGER NOT NULL,
                                       num_symbols                  INTEGER NOT NULL,
                                       test_set_id                  TEXT,

                                       participant_role             TEXT    NOT NULL CHECK (participant_role IN ('designer', 'player')),
                                       participant_id               TEXT    NOT NULL,
                                       participant_name             TEXT    NOT NULL,
                                       participant_type             TEXT    NOT NULL,
                                       participant_llm_model        TEXT,
                                       participant_llm_model_params JSONB,

                                       final_score                  INTEGER NOT NULL,
                                       in_game_designer_score       DECIMAL(10, 2)   DEFAULT 0.0,
                                       meta_designer_score          DECIMAL(10, 2)   DEFAULT 0.0,
                                       rank_in_game                 INTEGER NOT NULL,
                                       rank_in_game_incl_designer   INTEGER NOT NULL,

                                       observation_count            INTEGER          DEFAULT 0,
                                       observation_rounds           INTEGER          DEFAULT 0,
                                       did_quit                     INTEGER          DEFAULT 0 CHECK (did_quit IN (0, 1)),

                                       final_guess                  JSONB,
                                       action_log                   JSONB,
                                       queried_cells                JSONB,
                                       master_pattern               JSONB   NOT NULL,

                                       input_tokens                 INTEGER          DEFAULT 0,
                                       output_tokens                INTEGER          DEFAULT 0,

                                       created_at                   TIMESTAMPTZ      DEFAULT NOW(),

                                       game_config_dump             JSONB,
                                       designer_pattern_mode        TEXT,
                                       designer_description         TEXT
                                   )
                                   """)

                # Create indexes
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_analytics_game_id ON game_analytics(game_id)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_game_analytics_participant_id ON game_analytics(participant_id)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_game_analytics_role ON game_analytics(participant_role)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_game_analytics_model ON game_analytics(participant_llm_model)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_game_analytics_test_set ON game_analytics(test_set_id)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_game_analytics_created_at ON game_analytics(created_at DESC)")

                logger.info("game_analytics table created successfully")
            else:
                # Ensure all columns exist (idempotent migrations)
                for col, col_def in [
                    ('in_game_designer_score', 'DECIMAL(10,2) DEFAULT 0.0'),
                    ('meta_designer_score', 'DECIMAL(10,2) DEFAULT 0.0'),
                    ('game_config_dump', 'JSONB'),
                    ('designer_pattern_mode', 'TEXT'),
                    ('designer_description', 'TEXT'),
                ]:
                    col_exists = await conn.fetchval("""
                        SELECT EXISTS (SELECT FROM information_schema.columns
                                       WHERE table_name = 'game_analytics' AND column_name = $1)
                    """, col)
                    if not col_exists:
                        logger.info(f"Adding column {col} to game_analytics...")
                        await conn.execute(f"ALTER TABLE game_analytics ADD COLUMN {col} {col_def}")

                # Drop all FK constraints from game_analytics (make it standalone)
                fk_names = await conn.fetch("""
                    SELECT constraint_name FROM information_schema.table_constraints
                    WHERE table_name = 'game_analytics' AND constraint_type = 'FOREIGN KEY'
                """)
                for fk_row in fk_names:
                    fk_name = fk_row['constraint_name']
                    logger.info(f"Dropping FK constraint '{fk_name}' from game_analytics...")
                    await conn.execute(f"ALTER TABLE game_analytics DROP CONSTRAINT IF EXISTS {fk_name}")

            # ── Step 2: Find old tables (games / game_players, or _archived versions) ──
            # Support both original and already-renamed tables
            games_tbl = None
            for tbl_name in ['games', 'games_archived']:
                exists = await conn.fetchval("""
                    SELECT EXISTS (SELECT FROM information_schema.tables
                                   WHERE table_name = $1 AND table_schema = 'public')
                """, tbl_name)
                if exists:
                    games_tbl = tbl_name
                    break

            gp_tbl = None
            for tbl_name in ['game_players', 'game_players_archived']:
                exists = await conn.fetchval("""
                    SELECT EXISTS (SELECT FROM information_schema.tables
                                   WHERE table_name = $1 AND table_schema = 'public')
                """, tbl_name)
                if exists:
                    gp_tbl = tbl_name
                    break

            # ── Step 3: Full data migration from old tables → game_analytics ──
            if games_tbl and gp_tbl:
                logger.info(f"Found legacy tables: {games_tbl}, {gp_tbl}. Starting full migration...")

                # 3a: Update game_config_dump / designer_pattern_mode for existing rows
                updated = await conn.execute(f"""
                    UPDATE game_analytics ga
                    SET game_config_dump = g.game_config_dump,
                        designer_pattern_mode = g.designer_pattern_mode
                    FROM {games_tbl} g
                    WHERE ga.game_id = g.id
                      AND ga.game_config_dump IS NULL
                      AND g.game_config_dump IS NOT NULL
                """)
                logger.info(f"Updated game_config_dump for existing rows: {updated}")

                # 3b: Find game_ids in old tables that have NO rows in game_analytics
                missing_game_ids = await conn.fetch(f"""
                    SELECT g.id FROM {games_tbl} g
                    WHERE NOT EXISTS (
                        SELECT 1 FROM game_analytics ga WHERE ga.game_id = g.id
                    )
                """)
                logger.info(f"Found {len(missing_game_ids)} games in {games_tbl} not yet in game_analytics")

                # Helper: ensure value is JSON string for JSONB columns
                def to_json_str(val):
                    if val is None:
                        return None
                    if isinstance(val, str):
                        return val
                    return json.dumps(val)

                # 3c: Backfill missing games
                migrated_count = 0
                for row in missing_game_ids:
                    game_id = row['id']
                    try:
                        # Fetch game info
                        game = await conn.fetchrow(f"""
                            SELECT id, grid_size, num_symbols, designer_type,
                                   designer_llm_model, designer_llm_model_params,
                                   designer_pattern_mode, master_pattern, game_config_dump,
                                   test_set_id, created_at
                            FROM {games_tbl} WHERE id = $1
                        """, game_id)
                        if not game:
                            continue

                        # Fetch all players for this game
                        players = await conn.fetch(f"""
                            SELECT player_name_in_game, player_type, player_llm_model,
                                   player_llm_model_params, final_score, final_guess,
                                   action_log, queried_cells, input_tokens, output_tokens
                            FROM {gp_tbl} WHERE game_id = $1
                        """, game_id)

                        if not players:
                            continue

                        # Calculate ranks
                        player_scores = []
                        for p in players:
                            p_params = p['player_llm_model_params']
                            if p_params and not isinstance(p_params, dict):
                                try:
                                    p_params = json.loads(p_params)
                                except Exception:
                                    p_params = {}
                            p_id = f"{p['player_llm_model'] or 'unknown'}#{json.dumps(p_params or {})}"
                            player_scores.append((p_id, p['final_score'] or 0))

                        player_ranks = calculate_ranks(player_scores)

                        # Calculate designer scores
                        score_values = [s for _, s in player_scores]
                        in_game_ds, meta_ds = calculate_designer_scores(
                            score_values, game['grid_size'], game['num_symbols']
                        )

                        # Extract designer description from game_config_dump
                        _config_dump = game['game_config_dump']
                        if _config_dump and isinstance(_config_dump, str):
                            try:
                                _config_dump = json.loads(_config_dump)
                            except Exception:
                                _config_dump = None
                        _designer_desc = None
                        if isinstance(_config_dump, dict):
                            _designer_desc = (_config_dump.get('designer') or {}).get('description')

                        # Insert player rows
                        for p in players:
                            p_params_raw = p['player_llm_model_params']
                            if p_params_raw and not isinstance(p_params_raw, dict):
                                try:
                                    p_params_parsed = json.loads(p_params_raw)
                                except Exception:
                                    p_params_parsed = {}
                            else:
                                p_params_parsed = p_params_raw or {}
                            p_id = f"{p['player_llm_model'] or 'unknown'}#{json.dumps(p_params_parsed)}"
                            rank = player_ranks.get(p_id, len(player_scores))

                            # Parse action_log for observation stats
                            action_log_val = p['action_log']
                            if action_log_val and not isinstance(action_log_val, list):
                                try:
                                    action_log_val = json.loads(action_log_val)
                                except Exception:
                                    action_log_val = None
                            obs_count, obs_rounds, did_quit = analyze_action_log(action_log_val)

                            await conn.execute("""
                                INSERT INTO game_analytics (
                                    game_id, grid_size, num_symbols, test_set_id,
                                    participant_role, participant_id, participant_name, participant_type,
                                    participant_llm_model, participant_llm_model_params,
                                    final_score, in_game_designer_score, meta_designer_score,
                                    rank_in_game, rank_in_game_incl_designer,
                                    observation_count, observation_rounds, did_quit,
                                    final_guess, action_log, queried_cells, master_pattern,
                                    input_tokens, output_tokens,
                                    game_config_dump, designer_pattern_mode, created_at,
                                    designer_description
                                ) VALUES (
                                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28
                                )
                            """,
                                game_id, game['grid_size'], game['num_symbols'], game['test_set_id'],
                                'player', p_id, p['player_name_in_game'], p['player_type'],
                                p['player_llm_model'], to_json_str(p['player_llm_model_params']),
                                p['final_score'] or 0, 0.0, 0.0,
                                rank, rank,
                                obs_count, obs_rounds, did_quit,
                                to_json_str(p['final_guess']), to_json_str(p['action_log']),
                                to_json_str(p['queried_cells']), to_json_str(game['master_pattern']),
                                p['input_tokens'] or 0, p['output_tokens'] or 0,
                                to_json_str(game['game_config_dump']), game['designer_pattern_mode'],
                                game['created_at'],
                                _designer_desc
                            )

                        # Insert designer row if LLM designer
                        if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                            d_params = game['designer_llm_model_params']
                            if d_params and not isinstance(d_params, dict):
                                try:
                                    d_params_parsed = json.loads(d_params)
                                except Exception:
                                    d_params_parsed = {}
                            else:
                                d_params_parsed = d_params or {}
                            d_id = f"{game['designer_llm_model']}#{json.dumps(d_params_parsed)}"

                            await conn.execute("""
                                INSERT INTO game_analytics (
                                    game_id, grid_size, num_symbols, test_set_id,
                                    participant_role, participant_id, participant_name, participant_type,
                                    participant_llm_model, participant_llm_model_params,
                                    final_score, in_game_designer_score, meta_designer_score,
                                    rank_in_game, rank_in_game_incl_designer,
                                    observation_count, observation_rounds, did_quit,
                                    final_guess, action_log, queried_cells, master_pattern,
                                    input_tokens, output_tokens,
                                    game_config_dump, designer_pattern_mode, created_at,
                                    designer_description
                                ) VALUES (
                                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28
                                )
                            """,
                                game_id, game['grid_size'], game['num_symbols'], game['test_set_id'],
                                'designer', d_id, 'Designer', game['designer_type'],
                                game['designer_llm_model'], to_json_str(game['designer_llm_model_params']),
                                int(in_game_ds), in_game_ds, meta_ds,
                                0, 0,
                                0, 0, 0,
                                None, None, None, to_json_str(game['master_pattern']),
                                0, 0,
                                to_json_str(game['game_config_dump']), game['designer_pattern_mode'],
                                game['created_at'],
                                _designer_desc
                            )

                        migrated_count += 1
                    except Exception as e:
                        logger.error(f"Failed to migrate game {game_id}: {e}", exc_info=True)
                        continue

                logger.info(f"Backfill complete: migrated {migrated_count} games into game_analytics")

                # 3d: Verify migration
                ga_game_count = await conn.fetchval("SELECT COUNT(DISTINCT game_id) FROM game_analytics")
                old_game_count = await conn.fetchval(f"SELECT COUNT(*) FROM {games_tbl}")
                logger.info(f"Verification: game_analytics has {ga_game_count} games, {games_tbl} has {old_game_count} games")

                # ── Step 4: Drop all FK constraints from old tables, then DROP them ──
                # Drop FKs from game_players first (it references games)
                gp_fks = await conn.fetch(f"""
                    SELECT constraint_name FROM information_schema.table_constraints
                    WHERE table_name = '{gp_tbl}' AND constraint_type = 'FOREIGN KEY'
                """)
                for fk_row in gp_fks:
                    await conn.execute(f"ALTER TABLE {gp_tbl} DROP CONSTRAINT IF EXISTS {fk_row['constraint_name']}")

                logger.info(f"Dropping legacy table: {gp_tbl}...")
                await conn.execute(f"DROP TABLE IF EXISTS {gp_tbl} CASCADE")
                logger.info(f"Dropping legacy table: {games_tbl}...")
                await conn.execute(f"DROP TABLE IF EXISTS {games_tbl} CASCADE")
                logger.info("Legacy tables dropped. Database now has 3 tables only.")

            elif games_tbl and not gp_tbl:
                # games exists but game_players already gone — just drop games
                logger.info(f"Only {games_tbl} remains (no game_players). Dropping it...")
                await conn.execute(f"DROP TABLE IF EXISTS {games_tbl} CASCADE")
            elif not games_tbl and gp_tbl:
                # game_players exists but games already gone — just drop game_players
                logger.info(f"Only {gp_tbl} remains (no games). Dropping it...")
                await conn.execute(f"DROP TABLE IF EXISTS {gp_tbl} CASCADE")
            else:
                logger.info("No legacy tables found. Database is already clean (3 tables).")

            # Ensure indexes on active tables
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_analytics_game_id ON game_analytics(game_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_analytics_test_set ON game_analytics(test_set_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_analytics_created_at ON game_analytics(created_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_created_at ON test_sets(created_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_status ON test_sets(status)")

            logger.info("Database tables ready")

    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")


# --- FastAPI Application Initialization ---
app = FastAPI(title="Patterns II Game Backend", lifespan=lifespan, redirect_slashes=False)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Capture and format Pydantic validation errors, return unified JSON format.
    """
    error_messages = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        message = error["msg"]
        error_messages.append(f"Field '{field}': {message}")

    detail = "; ".join(error_messages)
    logger.error(f"Request validation failed: {detail}")

    return JSONResponse(
        status_code=422,
        content={"detail": detail},
    )


# Import all models from models.py
from models import (
    Symbol, Cell, Grid, ALL_SYMBOLS_PY,
    PositionModel, OpenRouterModel, ModelsListResponse,
    LLMModelParams, LLMPlayerTurnRequest, LLMPlayerTurnResponse,
    DesignPatternRequest, DesignPatternResponse,
    PlayerStateInGame, GameCreateRequest, GameCreateResponse,
    GameSummaryItem, GamePlayerDetailResponse, GameDetailResponse,
    TestSetParticipant, TestSetGameConfig, TestSetCreateRequest, TestSetCreateResponse,
    TestSetListResponse, TestSetStatusUpdateRequest,
    LeaderboardEntry, LeaderboardResponse,
    HumanPlayerActionRequest,
    JoinRequest, JoinResponse, ScoreboardEntry, ScoreboardResponse,
)



# --- Database connection ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/patterns_db")
db_pool = None

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://*.vercel.app", "https://patterns2.vercel.app",
                   "https://www.haozhang.site"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# --- Chat History Management Functions ---

def get_chat_history_key(game_id: str, player_id: str) -> str:
    """Generate unique key for chat history."""
    return f"{game_id}:{player_id}"


def get_player_messages(game_id: str, player_id: str) -> List[Dict[str, str]]:
    """Get message history for a specific player."""
    key = get_chat_history_key(game_id, player_id)
    with chat_histories_lock:
        return chat_histories[key].copy()


def append_message(game_id: str, player_id: str, role: str, content: str):
    """Append message to player's message history."""
    key = get_chat_history_key(game_id, player_id)
    with chat_histories_lock:
        chat_histories[key].append({"role": role, "content": content})
        # Limit message history length to control token usage
        if len(chat_histories[key]) > 50:  # Keep recent 50 messages
            # Keep system messages and recent conversation
            system_messages = [msg for msg in chat_histories[key] if msg["role"] == "system"]
            recent_messages = [msg for msg in chat_histories[key] if msg["role"] != "system"][-40:]
            chat_histories[key] = system_messages + recent_messages


def initialize_player_chat(game_id: str, player_id: str, player_name: str, grid_size: int,
                           symbols_in_use: List[Symbol],
                           evolving_context: Optional[str] = None):
    """Initialize the player's chat history.

    Args:
        evolving_context: Optional evolving context from previous games in the test set.
    """
    key = get_chat_history_key(game_id, player_id)
    with chat_histories_lock:
        if key not in chat_histories:
            system_message = prompt_manager.get_scientist_system(
                player_name=player_name,
                grid_size=grid_size,
                symbols_in_use=symbols_in_use,
            )
            if evolving_context:
                system_message += f"\n\n--- EXPERIENCE FROM PREVIOUS GAMES ---\n{evolving_context}"
            chat_histories[key] = [{"role": "system", "content": system_message}]


# --- Token Usage Tracking Functions ---

def get_player_token_key(game_id: str, player_id: str) -> str:
    """Generate unique key for player token tracking."""
    return f"{game_id}:{player_id}"


def add_player_token_usage(game_id: str, player_id: str, input_tokens: int, output_tokens: int):
    """Add player's token usage."""
    key = get_player_token_key(game_id, player_id)
    with player_token_usage_lock:
        player_token_usage[key]["input_tokens"] += input_tokens
        player_token_usage[key]["output_tokens"] += output_tokens
        logger.info(
            f"Player {player_id} cumulative token usage: input {player_token_usage[key]['input_tokens']}, output {player_token_usage[key]['output_tokens']}")


def get_player_token_usage(game_id: str, player_id: str) -> Tuple[int, int]:
    """Get player's cumulative token usage."""
    key = get_player_token_key(game_id, player_id)
    with player_token_usage_lock:
        return player_token_usage[key]["input_tokens"], player_token_usage[key]["output_tokens"]


def clear_player_token_usage(game_id: str, player_id: str):
    """Clear player's token usage record."""
    key = get_player_token_key(game_id, player_id)
    with player_token_usage_lock:
        if key in player_token_usage:
            del player_token_usage[key]


# --- LLM Player Core Functions ---

def build_llm_player_prompt(
        grid_size: int,
        symbols_in_use: List[Symbol],
        current_grid: List[List[str]],
        turn_number: int,
        player_name: str
) -> str:
    """Build the LLM player's per-turn prompt."""
    grid_display = format_grid_for_display(current_grid, grid_size)
    total_cells = grid_size * grid_size
    observed_cells = sum(1 for row in current_grid for cell in row if cell != "?" and cell is not None)
    unknown_cells = total_cells - observed_cells

    return prompt_manager.get_scientist_turn(
        grid_size=grid_size,
        symbols_in_use=symbols_in_use,
        grid_display=grid_display,
        turn_number=turn_number,
        observed_cells=observed_cells,
        total_cells=total_cells,
        unknown_cells=unknown_cells,
    )


def build_error_correction_prompt(
        grid_size: int,
        symbols_in_use: List[Symbol],
        error_message: str,
        current_grid: Optional[List[List[str]]] = None,
        turn_number: int = 0
) -> str:
    """Build the scientist error correction prompt with current grid state."""
    grid_display = ""
    observed_cells = 0
    total_cells = grid_size * grid_size
    unknown_cells = total_cells

    if current_grid:
        grid_display = format_grid_for_display(current_grid, grid_size)
        observed_cells = sum(1 for row in current_grid for cell in row if cell != "?" and cell is not None)
        unknown_cells = total_cells - observed_cells

    return prompt_manager.get_scientist_error_correction(
        grid_size=grid_size,
        symbols_in_use=symbols_in_use,
        error_message=error_message,
        grid_display=grid_display,
        observed_cells=observed_cells,
        total_cells=total_cells,
        unknown_cells=unknown_cells,
        turn_number=turn_number,
    )


def validate_observe_targets_are_unknown(
        response: LLMPlayerTurnResponse,
        current_grid: List[List[str]]
) -> None:
    """Treat observes of already revealed cells as invalid so retry can correct them."""
    if response.action != "observe" or not response.cellsToObserve:
        return

    already_observed_targets = []
    for cell in response.cellsToObserve:
        row, col = cell.row, cell.col
        if current_grid[row][col] != "?":
            already_observed_targets.append(f"({row},{col})={current_grid[row][col]}")

    if already_observed_targets:
        raise ValueError(
            "Observe target was already observed: "
            + ", ".join(already_observed_targets)
            + ". Choose only cells marked '?' in the current grid."
        )


def build_design_error_correction_prompt(
        grid_size: int,
        num_symbols: int,
        available_symbols: List[str],
        error_message: str
) -> str:
    """Build the designer error correction prompt (concise)."""
    return prompt_manager.get_designer_error_correction(
        grid_size=grid_size,
        num_symbols=num_symbols,
        available_symbols=available_symbols,
        error_message=error_message,
    )


async def llm_player_turn_logic_with_retry(request: LLMPlayerTurnRequest,
                                           max_retries: int = 10) -> Tuple[LLMPlayerTurnResponse, int, int]:
    """LLM player turn logic with retry mechanism. Returns response and token usage."""
    last_error = None
    last_response = None
    total_input_tokens = 0
    total_output_tokens = 0

    for retry_count in range(max_retries + 1):
        try:
            if retry_count == 0:
                # First attempt, use normal logic
                response, input_tokens, output_tokens = await llm_player_turn_logic(request)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

                # Record token usage
                add_player_token_usage(request.gameId, request.playerId, input_tokens, output_tokens)

                return response, total_input_tokens, total_output_tokens
            else:
                # Retry with error correction
                logger.info(f"LLM player {request.playerName} retry #{retry_count}")

                # Build error correction prompt with current grid state
                error_prompt = build_error_correction_prompt(
                    request.gridSize,
                    request.symbolsInUse,
                    str(last_error),
                    current_grid=request.currentGrid,
                    turn_number=request.turnNumber,
                )

                # Add error correction message to chat history
                append_message(request.gameId, request.playerId, "user", error_prompt)

                # Get full message history for API call
                messages = get_player_messages(request.gameId, request.playerId)

                # Call LLM via unified client
                llm_resp = await llm_client.chat(
                    model=request.llmModel,
                    messages=messages,
                    params=request.llmModelParams,
                )

                total_input_tokens += llm_resp.input_tokens
                total_output_tokens += llm_resp.output_tokens

                # Record token usage
                add_player_token_usage(request.gameId, request.playerId, llm_resp.input_tokens, llm_resp.output_tokens)

                last_response = llm_resp.content

                # Add LLM response to chat history
                append_message(request.gameId, request.playerId, "assistant", llm_resp.content)

                # Parse LLM response
                parsed_response = parse_llm_response(
                    llm_resp.content,
                    request.gridSize,
                    request.symbolsInUse
                )
                validate_observe_targets_are_unknown(parsed_response, request.currentGrid)

                logger.info(f"LLM player {request.playerName} retry #{retry_count} succeeded")
                return parsed_response, total_input_tokens, total_output_tokens

        except Exception as e:
            last_error = e
            last_response = getattr(e, 'response_content', last_response)
            logger.warning(f"LLM player {request.playerName} attempt #{retry_count + 1} failed: {e}")

            if retry_count == max_retries:
                logger.error(f"LLM player {request.playerName} reached max retries ({max_retries}), giving up")
                raise Exception(f"Maximum retries ({max_retries}) reached. Last error: {str(last_error)}")

            await asyncio.sleep(0.3)

    raise Exception("Unexpected error in retry logic")


async def design_pattern_logic_with_retry(request: DesignPatternRequest, max_retries: int = 10) -> DesignPatternResponse:
    """Design pattern logic with retry mechanism."""
    last_error = None
    last_response = None

    for retry_count in range(max_retries + 1):
        try:
            if retry_count == 0:
                return await design_pattern_logic(request)
            else:
                logger.info(f"Design pattern retry #{retry_count}")

                current_symbols = ALL_SYMBOLS_PY[:request.numSymbols]

                # Build concise error correction prompt
                error_prompt = build_design_error_correction_prompt(
                    request.gridSize,
                    request.numSymbols,
                    current_symbols,
                    str(last_error),
                )

                # Use higher temperature for design creativity (at least 0.7)
                temp_override = 0.7
                if request.llmModelParams and request.llmModelParams.temperature is not None and request.llmModelParams.temperature >= 0.5:
                    temp_override = None

                # Call LLM via unified client
                llm_resp = await llm_client.chat(
                    model=request.llmModel,
                    messages=[{"role": "user", "content": error_prompt}],
                    params=request.llmModelParams,
                    temperature_override=temp_override,
                )

                last_response = llm_resp.content

                try:
                    parsed_response = json.loads(strip_markdown_json(llm_resp.content))
                except json.JSONDecodeError:
                    # Try to fix truncated JSON
                    fixed = try_fix_truncated_json(llm_resp.content)
                    if fixed is not None:
                        logger.info("Auto-fixed truncated designer JSON response")
                        parsed_response = fixed
                    else:
                        raise
                llm_pattern = parsed_response.get("pattern")
                llm_description = parsed_response.get("description")

                if not llm_pattern:
                    raise ValueError("LLM response missing 'pattern' field")

                validated_pattern = validate_pattern(llm_pattern, request.gridSize, current_symbols)

                logger.info(f"Design pattern retry #{retry_count} succeeded")
                return DesignPatternResponse(pattern=validated_pattern, description=llm_description)

        except Exception as e:
            last_error = e
            last_response = getattr(e, 'response_content', last_response)
            logger.warning(f"Design pattern attempt #{retry_count + 1} failed: {e}")

            if retry_count == max_retries:
                logger.error(f"Design pattern reached max retries ({max_retries}), giving up")
                raise Exception(f"Maximum retries ({max_retries}) reached. Last error: {str(last_error)}")

            await asyncio.sleep(0.3)

    raise Exception("Unexpected error in design pattern retry logic")


async def llm_player_turn_logic(request: LLMPlayerTurnRequest) -> Tuple[LLMPlayerTurnResponse, int, int]:
    """LLM player turn logic - extracted from endpoint for background task use. Returns response and token usage."""
    # Check if chat history (multi-turn) is enabled
    chat_history_enabled = False
    if request.llmModelParams and request.llmModelParams.chatHistoryEnabled:
        chat_history_enabled = True

    # Initialize player chat history (if first time)
    initialize_player_chat(
        request.gameId,
        request.playerId,
        request.playerName,
        request.gridSize,
        request.symbolsInUse
    )

    # Build current turn prompt
    current_prompt = build_llm_player_prompt(
        request.gridSize,
        request.symbolsInUse,
        request.currentGrid,
        request.turnNumber,
        request.playerName
    )

    if chat_history_enabled:
        # Multi-turn: accumulate full chat history
        append_message(request.gameId, request.playerId, "user", current_prompt)
        messages = get_player_messages(request.gameId, request.playerId)
    else:
        # Single-turn (default): only system + current user message
        system_messages = [msg for msg in get_player_messages(request.gameId, request.playerId)
                          if msg["role"] == "system"]
        messages = system_messages + [{"role": "user", "content": current_prompt}]

    # Call LLM via unified client
    llm_resp = await llm_client.chat(
        model=request.llmModel,
        messages=messages,
        params=request.llmModelParams,
    )

    if chat_history_enabled:
        # Multi-turn: save assistant response to history
        append_message(request.gameId, request.playerId, "assistant", llm_resp.content)

    # Parse LLM response
    parsed_response = parse_llm_response(
        llm_resp.content,
        request.gridSize,
        request.symbolsInUse
    )
    validate_observe_targets_are_unknown(parsed_response, request.currentGrid)

    return parsed_response, llm_resp.input_tokens, llm_resp.output_tokens


def format_grid_for_display(grid: List[List[str]], grid_size: int) -> str:
    """Format grid for readable display using unified coordinate system."""
    if not grid or len(grid) == 0:
        return "Empty grid"

    # Create column headers (0, 1, 2, ...)
    header = "    " + "   ".join(str(i) for i in range(grid_size))
    lines = [header]

    # Add separator line
    separator = "  " + "---" * grid_size + "-" * (grid_size - 1)
    lines.append(separator)

    # Add each row (row number on left)
    for i in range(grid_size):
        if i < len(grid):
            row_data = []
            for j in range(grid_size):
                if j < len(grid[i]):
                    cell = grid[i][j]
                    if cell is None or cell == "?" or cell == "null" or cell == "undefined":
                        row_data.append(" ? ")
                    else:
                        row_data.append(f" {cell} ")
                else:
                    row_data.append(" ? ")
            row_str = f"{i} |" + "|".join(row_data) + "|"
        else:
            row_str = f"{i} |" + "|".join([" ? "] * grid_size) + "|"
        lines.append(row_str)

    return "\n".join(lines)


def parse_llm_response(response_text: str, grid_size: int, symbols_in_use: List[Symbol]) -> LLMPlayerTurnResponse:
    """Parse LLM's JSON response."""
    try:
        response_data = json.loads(strip_markdown_json(response_text))
    except json.JSONDecodeError as e:
        # Try to fix truncated JSON (e.g. reasoning field cut off by token limit)
        fixed = try_fix_truncated_json(response_text)
        if fixed is not None:
            logger.info("Auto-fixed truncated JSON response")
            response_data = fixed
        else:
            raise ValueError(f"Invalid JSON response: {e}")

    action = response_data.get("action")
    if action not in ["observe", "guess", "give_up"]:
        raise ValueError(f"Invalid action: {action}")

    reasoning = response_data.get("reasoning", "No reasoning provided")
    confidence = response_data.get("confidence")

    cells_to_observe = None
    guess_grid = None

    if action == "observe":
        cells_data = response_data.get("cellsToObserve", [])
        cells_to_observe = []

        for cell_data in cells_data:
            if isinstance(cell_data, dict) and "row" in cell_data and "col" in cell_data:
                row, col = cell_data["row"], cell_data["col"]
                if 0 <= row < grid_size and 0 <= col < grid_size:
                    cells_to_observe.append(PositionModel(row=row, col=col))

        if not cells_to_observe:
            raise ValueError("Observe action requires valid cells to observe")

    elif action == "guess":
        guess_data = response_data.get("guessGrid")
        if not guess_data:
            raise ValueError("Guess action requires guessGrid")

        # Validate guess grid
        if not isinstance(guess_data, list) or len(guess_data) != grid_size:
            raise ValueError(f"GuessGrid must be a {grid_size}×{grid_size} array")

        guess_grid = []
        for i, row in enumerate(guess_data):
            if not isinstance(row, list) or len(row) != grid_size:
                raise ValueError(f"Row {i} must have {grid_size} elements")

            validated_row = []
            for j, cell in enumerate(row):
                if cell not in symbols_in_use:
                    raise ValueError(f"Invalid symbol '{cell}' at position ({i},{j})")
                validated_row.append(cell)
            guess_grid.append(validated_row)

    return LLMPlayerTurnResponse(
        action=action,
        cellsToObserve=cells_to_observe,
        guessGrid=guess_grid,
        reasoning=reasoning,
        confidence=confidence
    )


def validate_pattern(pattern: Any, expected_size: int, valid_symbols: List[str]) -> Grid:
    """Validate LLM-generated pattern meets requirements."""
    if not isinstance(pattern, list) or len(pattern) != expected_size:
        raise ValueError(f"Pattern must be a {expected_size}x{expected_size} list")

    validated_grid: Grid = []
    for row_idx, row in enumerate(pattern):
        validated_row: List[Cell] = []

        # Handle string format
        if isinstance(row, str):
            if len(row) != expected_size:
                raise ValueError(f"Row {row_idx} must have {expected_size} characters, got {len(row)}")

            for col_idx, char in enumerate(row):
                if char not in valid_symbols:
                    raise ValueError(f"Invalid symbol '{char}' at position ({row_idx},{col_idx})")
                validated_row.append(char)

        # Handle array format
        elif isinstance(row, list):
            if len(row) != expected_size:
                raise ValueError(f"Row {row_idx} must have {expected_size} elements, got {len(row)}")

            for col_idx, cell in enumerate(row):
                if cell not in valid_symbols:
                    raise ValueError(f"Invalid symbol '{cell}' at position ({row_idx},{col_idx})")
                validated_row.append(cell)

        else:
            raise ValueError(f"Row {row_idx} must be either a string or a list, got {type(row)}")

        validated_grid.append(validated_row)

    return validated_grid


def build_system_prompt(grid_size: int, num_symbols: int, available_symbols: List[str]) -> str:
    """Build the LLM designer system prompt."""
    return prompt_manager.get_designer_system(
        grid_size=grid_size,
        num_symbols=num_symbols,
        available_symbols=available_symbols,
    )


def build_user_prompt(user_prompt: Optional[str]) -> str:
    """Build the designer user prompt."""
    return prompt_manager.get_designer_user(user_prompt=user_prompt)


# --- API Endpoint Logic Functions (for background tasks) ---

async def design_pattern_logic(request: DesignPatternRequest) -> DesignPatternResponse:
    """Design pattern logic - extracted from endpoint for background task use."""
    current_symbols = ALL_SYMBOLS_PY[:request.numSymbols]
    system_prompt = build_system_prompt(request.gridSize, request.numSymbols, current_symbols)
    user_prompt = build_user_prompt(request.prompt)

    # Use higher temperature for design creativity (at least 0.7)
    temp_override = 0.7
    if request.llmModelParams and request.llmModelParams.temperature is not None and request.llmModelParams.temperature >= 0.5:
        temp_override = None  # User's temperature is high enough

    # Call LLM via unified client
    llm_resp = await llm_client.chat(
        model=request.llmModel,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        params=request.llmModelParams,
        temperature_override=temp_override,
    )

    try:
        parsed_response = json.loads(strip_markdown_json(llm_resp.content))
    except json.JSONDecodeError:
        fixed = try_fix_truncated_json(llm_resp.content)
        if fixed is not None:
            logger.info("Auto-fixed truncated designer JSON response")
            parsed_response = fixed
        else:
            raise
    llm_pattern = parsed_response.get("pattern")
    llm_description = parsed_response.get("description")

    if not llm_pattern:
        raise ValueError("LLM response missing 'pattern' field")

    validated_pattern = validate_pattern(llm_pattern, request.gridSize, current_symbols)

    return DesignPatternResponse(pattern=validated_pattern, description=llm_description)


async def save_game_logic(request: GameCreateRequest, override_game_id: Optional[str] = None) -> GameCreateResponse:
    """Core implementation of save game logic.

    If override_game_id is provided, uses that instead of generating a new one.
    This allows grouping multiple human players into the same game.
    """
    if not db_pool:
        raise Exception("Database not connected")

    async with db_pool.acquire() as conn:
        game_id = override_game_id or str(uuid.uuid4())

        # Serialize designer model params
        designer_params_json = None
        if request.designer_llm_model_params:
            designer_params_json = json.dumps(request.designer_llm_model_params.dict(exclude_none=True))

        # [ARCHIVED] INSERT INTO games — game_analytics is now the single source of truth
        # await conn.execute(
        #     """
        #     INSERT INTO games (id, grid_size, num_symbols, designer_type, designer_llm_model, designer_llm_model_params,
        #                        designer_pattern_mode, master_pattern, game_config_dump, test_set_id)
        #     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        #     """,
        #     game_id, request.grid_size, request.num_symbols, request.designer_type,
        #     request.designer_llm_model, designer_params_json, request.designer_pattern_mode,
        #     json.dumps(request.master_pattern), json.dumps(request.game_config_dump), request.test_set_id
        # )

        # First, collect all player scores for player-only ranking
        player_scores = []
        for player in request.players:
            # Create a unique ID for player including model and params
            player_params_dict = player.player_llm_model_params.dict(
                exclude_none=True) if player.player_llm_model_params else {}
            if player.player_type == 'Human':
                # Use player name as unique identifier for human players
                player_id = f"Human:{player.player_name_in_game}#{{}}"
            else:
                player_id = f"{player.player_llm_model or 'unknown_model'}#{json.dumps(player_params_dict)}"
            player_scores.append((player_id, player.final_score or 0))

        # Calculate ranks for players only
        player_ranks = calculate_ranks(player_scores)

        # Calculate designer score: 2 * (best - worst) - dropout penalty
        player_score_values = [score for _, score in player_scores]
        num_dropouts = 0
        for player in request.players:
            _, _, did_quit = analyze_action_log(player.action_log)
            if did_quit:
                num_dropouts += 1
        designer_score = calculate_designer_score(player_score_values, num_dropouts)

        logger.info(f"Game {game_id}: designer score: {designer_score:.2f} (dropouts: {num_dropouts})")

        # Collect all participant scores (players + designer) for overall ranking
        all_participant_scores = player_scores.copy()
        designer_input_tokens = 0
        designer_output_tokens = 0
        designer_id = None

        if request.designer_type == "LLM" and request.designer_llm_model:
            designer_params_dict = request.designer_llm_model_params.dict(
                exclude_none=True) if request.designer_llm_model_params else {}
            designer_id = f"{request.designer_llm_model}#{json.dumps(designer_params_dict)}"
            all_participant_scores.append((designer_id, designer_score))

        all_ranks = calculate_ranks(all_participant_scores)

        config_dump_json = json.dumps(request.game_config_dump) if request.game_config_dump else None
        # Extract designer description from game_config_dump
        _desc = (request.game_config_dump.get('designer') or {}).get('description') if request.game_config_dump else None

        for player in request.players:
            # Safety check for player_llm_model_params
            player_params_dict = player.player_llm_model_params.dict(
                exclude_none=True) if player.player_llm_model_params else {}
            if player.player_type == 'Human':
                player_id = f"Human:{player.player_name_in_game}#{{}}"
            else:
                player_id = f"{player.player_llm_model or 'unknown_model'}#{json.dumps(player_params_dict)}"

            # Get player rank (players only)
            player_rank = player_ranks.get(player_id, len(player_scores))

            player_rank_incl_designer = all_ranks.get(player_id, len(all_participant_scores))

            # Safety check for queried_cells
            queried_cells_json = None
            if player.queried_cells:
                try:
                    queried_cells_json = json.dumps([{"row": p.row, "col": p.col} for p in player.queried_cells])
                except Exception as e:
                    logger.warning(f"Failed to serialize queried_cells for player {player.player_name_in_game}: {e}")
                    queried_cells_json = None

            # Serialize player model params
            player_params_json = None
            if player.player_llm_model_params:
                player_params_json = json.dumps(player.player_llm_model_params.dict(exclude_none=True))

            # Use provided token data
            input_tokens = player.input_tokens or 0
            output_tokens = player.output_tokens or 0

            logger.info(f"Saving player {player.player_name_in_game} token data: input {input_tokens}, output {output_tokens}")

            # [ARCHIVED] INSERT INTO game_players — game_analytics is now the single source of truth
            # await conn.execute(
            #     """
            #     INSERT INTO game_players (game_id, player_name_in_game, player_type, player_llm_model,
            #                               player_llm_model_params,
            #                               final_score, final_guess, action_log, queried_cells, input_tokens,
            #                               output_tokens)
            #     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            #     """,
            #     game_id, player.player_name_in_game, player.player_type, player.player_llm_model, player_params_json,
            #     player.final_score,
            #     json.dumps(player.final_guess) if player.final_guess else None,
            #     json.dumps(player.action_log) if player.action_log else None,
            #     queried_cells_json,
            #     input_tokens, output_tokens
            # )

            await conn.execute(
                """
                INSERT INTO game_analytics (game_id, grid_size, num_symbols, test_set_id,
                                            participant_role, participant_id, participant_name, participant_type,
                                            participant_llm_model, participant_llm_model_params,
                                            final_score, in_game_designer_score, meta_designer_score,
                                            rank_in_game, rank_in_game_incl_designer,
                                            observation_count, observation_rounds, did_quit,
                                            final_guess, action_log, queried_cells, master_pattern,
                                            input_tokens, output_tokens,
                                            game_config_dump, designer_pattern_mode,
                                            designer_description)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                        $22, $23, $24, $25, $26, $27)
                """,
                game_id, request.grid_size, request.num_symbols, request.test_set_id,
                'player', player_id, player.player_name_in_game, player.player_type,
                player.player_llm_model if player.player_type == 'LLM' else f"Human:{player.player_name_in_game}",
                player_params_json,
                player.final_score, 0.0, 0.0,  # Players don't have designer scores
                player_rank, player_rank_incl_designer,
                analyze_action_log(player.action_log)[0], analyze_action_log(player.action_log)[1],
                analyze_action_log(player.action_log)[2],
                json.dumps(player.final_guess) if player.final_guess else None,
                json.dumps(player.action_log) if player.action_log else None,
                queried_cells_json,
                json.dumps(request.master_pattern),
                input_tokens, output_tokens,
                config_dump_json, request.designer_pattern_mode,
                _desc
            )

        if request.designer_type == "LLM" and request.designer_llm_model:
            # Serialize designer model parameters
            designer_params_json = None
            if request.designer_llm_model_params:
                designer_params_json = json.dumps(request.designer_llm_model_params.dict(exclude_none=True))

            designer_rank_incl_designer = all_ranks.get(designer_id, len(all_participant_scores))

            await conn.execute(
                """
                INSERT INTO game_analytics (game_id, grid_size, num_symbols, test_set_id,
                                            participant_role, participant_id, participant_name, participant_type,
                                            participant_llm_model, participant_llm_model_params,
                                            final_score, in_game_designer_score, meta_designer_score,
                                            rank_in_game, rank_in_game_incl_designer,
                                            observation_count, observation_rounds, did_quit,
                                            final_guess, action_log, queried_cells, master_pattern,
                                            input_tokens, output_tokens,
                                            game_config_dump, designer_pattern_mode,
                                            designer_description)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                        $22, $23, $24, $25, $26, $27)
                """,
                game_id, request.grid_size, request.num_symbols, request.test_set_id,
                'designer', designer_id, 'Designer', request.designer_type,
                request.designer_llm_model, designer_params_json,
                int(designer_score), designer_score, 0.0,
                0, designer_rank_incl_designer,
                0, 0, 0,
                None, None, None,
                json.dumps(request.master_pattern),
                designer_input_tokens, designer_output_tokens,
                config_dump_json, request.designer_pattern_mode,
                _desc
            )

        logger.info(f"Game saved successfully with ID: {game_id}")
        return GameCreateResponse(message="Game saved successfully", game_id=game_id)


# --- Model List Logic Functions ---

async def get_available_models_logic() -> ModelsListResponse:
    """Get available models - merge OpenRouter and OpenAI official models."""
    logger.info("Fetching model list...")

    all_models = []

    # Get OpenRouter models
    if openrouter_client:
        try:
            logger.info("Fetching models from OpenRouter API...")
            response_data = await openrouter_client.get("/models")
            response_json = response_data.json()

            if 'data' in response_json:
                models_data = response_json['data']
                logger.info(f"Fetched {len(models_data)} models from OpenRouter")

                # Filter suitable models
                for model in models_data:
                    try:
                        model_id = model.get('id', '')
                        model_name = model.get('name', model_id)

                        # Skip unsuitable models
                        if any(skip in model_id.lower() for skip in
                               ['embedding', 'whisper', 'tts', 'dall-e']):
                            continue

                        all_models.append(OpenRouterModel(
                            id=model_id,  # Keep original ID without prefix
                            name=model_name,
                            created=model.get('created', 0),
                            description=model.get('description'),
                            architecture=model.get('architecture'),
                            top_provider=model.get('top_provider'),
                            pricing=model.get('pricing'),
                            canonical_slug=model.get('canonical_slug'),
                            context_length=model.get('context_length'),
                            hugging_face_id=model.get('hugging_face_id'),
                            per_request_limits=model.get('per_request_limits'),
                            supported_parameters=model.get('supported_parameters')
                        ))
                    except Exception as e:
                        logger.warning(f"Skipping OpenRouter model {model.get('id', 'unknown')} due to error: {e}")
                        continue

        except Exception as e:
            logger.error(f"Failed to fetch OpenRouter models: {str(e)}")

    # Sort: keep the canonical default first, then alphabetical by ID.
    priority_models = ["openai/gpt-4o-mini"]

    def sort_key(model: OpenRouterModel):
        if model.id in priority_models:
            return (0, priority_models.index(model.id))
        return (1, model.id)

    all_models.sort(key=sort_key)

    logger.info(f"Total {len(all_models)} OpenRouter models available")

    # Return a small static fallback if OpenRouter is unreachable.
    if not all_models:
        logger.info("Returning default model list")
        default_models = [
            OpenRouterModel(id="openai/gpt-4o-mini", name="GPT-4o Mini", created=0),
            OpenRouterModel(id="openai/gpt-4o", name="GPT-4o", created=0),
            OpenRouterModel(id="anthropic/claude-sonnet-4", name="Claude Sonnet 4", created=0),
            OpenRouterModel(id="deepseek/deepseek-chat", name="DeepSeek Chat", created=0),
            OpenRouterModel(id="meta-llama/llama-4-scout", name="Llama 4 Scout", created=0),
            OpenRouterModel(id="x-ai/grok-4", name="Grok 4", created=0),
        ]
        return ModelsListResponse(object="list", data=default_models)

    return ModelsListResponse(object="list", data=all_models)


# --- API Endpoints ---

@app.get("/api/models", response_model=ModelsListResponse)
@app.post("/api/models", response_model=ModelsListResponse)
async def get_available_models():
    """Get available models - supports GET and POST."""
    logger.info("Received model list request")
    return await get_available_models_logic()


@app.post("/api/llm-player-turn", response_model=LLMPlayerTurnResponse)
async def llm_player_turn_endpoint(request: LLMPlayerTurnRequest):
    """LLM player single turn API."""
    logger.info(f"LLM player {request.playerName} turn {request.turnNumber} started")

    try:
        response, input_tokens, output_tokens = await llm_player_turn_logic_with_retry(request)
        logger.info(f"LLM player {request.playerName} used {input_tokens} input tokens, {output_tokens} output tokens")

        # Include token info in response
        response.input_tokens = input_tokens
        response.output_tokens = output_tokens

        return response

    except Exception as e:
        logger.error(f"LLM API error: {e}")
        error_message = str(e)

        if "insufficient_quota" in error_message.lower() or "quota" in error_message.lower():
            error_message = "API quota exceeded"
        elif "rate_limit" in error_message.lower():
            error_message = "API rate limit exceeded, please try again later"
        elif "max_completion_tokens" in error_message.lower():
            error_message = f"Parameter error: {error_message}. Please check model parameter configuration."

        raise HTTPException(status_code=502, detail=f"LLM API error: {error_message}")


@app.post("/api/design-pattern", response_model=DesignPatternResponse)
async def design_pattern_endpoint(request: DesignPatternRequest):
    """Generate game pattern using LLM."""
    try:
        return await design_pattern_logic_with_retry(request)

    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Invalid JSON from LLM: {str(e)}")

    except ValueError as e:
        logger.error(f"Pattern validation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Pattern validation failed: {str(e)}")

    except Exception as e:
        logger.error(f"Pattern design failed: {e}")
        raise HTTPException(status_code=500, detail=f"Pattern design failed: {str(e)}")


@app.post("/api/games", response_model=GameCreateResponse, status_code=201)
async def save_game_endpoint(request: GameCreateRequest):
    """Save completed game data."""
    try:
        return await save_game_logic(request)
    except Exception as e:
        logger.error(f"Failed to save game: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save game: {str(e)}")


@app.get("/api/games")
async def get_games_list_endpoint(limit: int = 50, offset: int = 0, test_set_id: Optional[str] = None):
    """Get game history list (reads from game_analytics)."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            if test_set_id:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (game_id) game_id, created_at, grid_size, num_symbols,
                           COALESCE(
                               (SELECT ga2.participant_type FROM game_analytics ga2
                                WHERE ga2.game_id = ga.game_id AND ga2.participant_role = 'designer'
                                LIMIT 1),
                               'Human'
                           ) as designer_type
                    FROM game_analytics ga
                    WHERE test_set_id = $1
                    ORDER BY game_id, created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    test_set_id, limit, offset
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (game_id) game_id, created_at, grid_size, num_symbols,
                           COALESCE(
                               (SELECT ga2.participant_type FROM game_analytics ga2
                                WHERE ga2.game_id = ga.game_id AND ga2.participant_role = 'designer'
                                LIMIT 1),
                               'Human'
                           ) as designer_type
                    FROM game_analytics ga
                    ORDER BY game_id, created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    limit, offset
                )

            games = []
            for row in rows:
                game_dict = {
                    'id': str(row['game_id']),
                    'created_at': row['created_at'].isoformat(),
                    'grid_size': row['grid_size'],
                    'num_symbols': row['num_symbols'],
                    'designer_type': row['designer_type']
                }
                games.append(game_dict)

            # Sort by created_at descending (DISTINCT ON uses game_id ordering)
            games.sort(key=lambda g: g['created_at'], reverse=True)

            logger.info(f"Retrieved {len(games)} games from game_analytics")
            return games

    except Exception as e:
        logger.error(f"Failed to get games list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get games list: {str(e)}")


@app.get("/api/games/history-for-evolving")
async def get_game_history_for_evolving(
    model_name: Optional[str] = None,
    test_set_id: Optional[str] = None,
    limit: int = 50
):
    """Get game history for evolving mode import. Filters by model_name or test_set_id."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            if model_name:
                rows = await conn.fetch(
                    """
                    SELECT ga.game_id, ga.grid_size, ga.num_symbols, ga.final_score,
                           ga.observation_count, ga.participant_role, ga.test_set_id,
                           ga.created_at
                    FROM game_analytics ga
                    WHERE ga.participant_llm_model = $1
                    ORDER BY ga.created_at DESC
                    LIMIT $2
                    """,
                    model_name, limit
                )
            elif test_set_id:
                rows = await conn.fetch(
                    """
                    SELECT ga.game_id, ga.grid_size, ga.num_symbols, ga.final_score,
                           ga.observation_count, ga.participant_role, ga.test_set_id,
                           ga.created_at
                    FROM game_analytics ga
                    WHERE ga.test_set_id = $1
                    ORDER BY ga.created_at ASC
                    """,
                    test_set_id
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT ga.game_id, ga.grid_size, ga.num_symbols, ga.final_score,
                           ga.observation_count, ga.participant_role, ga.test_set_id,
                           ga.created_at
                    FROM game_analytics ga
                    ORDER BY ga.created_at DESC
                    LIMIT $1
                    """,
                    limit
                )

            results = []
            for row in rows:
                results.append({
                    'game_id': str(row['game_id']),
                    'grid_size': row['grid_size'],
                    'num_symbols': row['num_symbols'],
                    'final_score': row['final_score'],
                    'observation_count': row['observation_count'] or 0,
                    'participant_role': row['participant_role'],
                    'test_set_id': row['test_set_id'],
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                })
            return results

    except Exception as e:
        logger.error(f"Failed to get game history for evolving: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/games/{game_id}")
async def get_game_details_endpoint(game_id: str):
    """Get game details from game_analytics table."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            # Get all analytics rows for this game
            all_rows = await conn.fetch(
                "SELECT * FROM game_analytics WHERE game_id = $1 ORDER BY participant_role DESC, final_score DESC",
                game_id
            )
            if not all_rows:
                raise HTTPException(status_code=404, detail="Game not found")

            # Separate player rows and designer row
            player_rows = [r for r in all_rows if r['participant_role'] == 'player']
            designer_row = next((r for r in all_rows if r['participant_role'] == 'designer'), None)

            # Use first row for game-level data
            first_row = all_rows[0]

            # Build players list
            players = []
            player_scores = []
            for row in player_rows:
                player_data = {
                    'player_name_in_game': row['participant_name'],
                    'player_type': row['participant_type'],
                    'player_llm_model': row['participant_llm_model'],
                    'player_llm_model_params': None,
                    'final_score': row['final_score'] or 0,
                    'final_guess': None,
                    'action_log': None,
                    'queried_cells': []
                }
                if row['final_score'] is not None:
                    player_scores.append(row['final_score'])
                if row['participant_llm_model_params']:
                    try:
                        player_data['player_llm_model_params'] = json.loads(row['participant_llm_model_params'])
                    except Exception:
                        pass
                if row['final_guess']:
                    try:
                        player_data['final_guess'] = json.loads(row['final_guess'])
                    except Exception:
                        pass
                if row['action_log']:
                    try:
                        player_data['action_log'] = json.loads(row['action_log'])
                    except Exception:
                        pass
                if row['queried_cells']:
                    try:
                        player_data['queried_cells'] = json.loads(row['queried_cells'])
                    except Exception:
                        player_data['queried_cells'] = []
                players.append(player_data)

            # Designer scores
            in_game_designer_score = None
            meta_designer_score = None
            designer_llm_model = None
            designer_llm_model_params = None
            designer_type = 'Human'

            if designer_row:
                designer_type = designer_row['participant_type'] or 'LLM'
                designer_llm_model = designer_row['participant_llm_model']
                in_game_designer_score = float(designer_row['in_game_designer_score'] or 0)
                meta_designer_score = float(designer_row['meta_designer_score'] or 0)
                if designer_row['participant_llm_model_params']:
                    try:
                        designer_llm_model_params = json.loads(designer_row['participant_llm_model_params'])
                    except Exception:
                        pass
            elif len(player_scores) > 0:
                # Human designer — recalculate scores if needed
                pass

            # Parse game-level fields
            master_pattern = json.loads(first_row['master_pattern']) if first_row['master_pattern'] else []
            game_config_dump = None
            if first_row['game_config_dump']:
                try:
                    game_config_dump = json.loads(first_row['game_config_dump'])
                except Exception:
                    pass

            game_data = {
                'id': str(first_row['game_id']),
                'created_at': first_row['created_at'].isoformat(),
                'grid_size': first_row['grid_size'],
                'num_symbols': first_row['num_symbols'],
                'designer_type': designer_type,
                'designer_llm_model': designer_llm_model,
                'designer_llm_model_params': designer_llm_model_params,
                'designer_pattern_mode': first_row.get('designer_pattern_mode'),
                'designer_description': first_row.get('designer_description'),
                'designer_score': in_game_designer_score,
                'master_pattern': master_pattern,
                'game_config_dump': game_config_dump,
                'players': players,
                'in_game_designer_score': in_game_designer_score,
                'meta_designer_score': meta_designer_score
            }

            return game_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get game details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get game details: {str(e)}")


# --- Test Set and Leaderboard APIs ---

@app.get("/api/human-names")
async def get_existing_human_names():
    """Get all existing human player names from the database."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT participant_name FROM game_analytics
                WHERE participant_type = 'Human' AND participant_role = 'player'
                """
            )
            names = [row['participant_name'] for row in rows]
            return {"names": names}
    except Exception as e:
        logger.error(f"Failed to get human names: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/test-sets")
async def create_test_set_endpoint(request: TestSetCreateRequest):
    """Create a test set."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        test_set_id = str(uuid.uuid4())

        # Calculate total games
        total_games = 0
        llm_participant_count = sum(1 for p in request.participants if p.participant_type == 'LLM')
        for game_config in request.games:
            if request.llm_rotate_designer:
                # Rotating designer: only LLM participants rotate as designer
                total_games += llm_participant_count * game_config.repeat_count
            else:
                # Fixed designer mode: only one game
                total_games += game_config.repeat_count

        config_data = {
            "participants": [
                {
                    "participant_type": p.participant_type,
                    "human_name": p.human_name,
                    "model_name": p.model_name,
                    "model_params": p.model_params.dict(exclude_none=True) if p.model_params else None,
                    "evolving_config": p.evolving_config.dict() if p.evolving_config else None,
                }
                for p in request.participants
            ],
            "llm_rotate_designer": request.llm_rotate_designer,
            "games": [
                {
                    "grid_size": g.grid_size,
                    "num_symbols": g.num_symbols,
                    "optional_prompt": g.optional_prompt,
                    "custom_pattern": g.custom_pattern,
                    "repeat_count": g.repeat_count,
                    "pattern_mode": g.pattern_mode,
                    "symmetry_type": g.symmetry_type,
                    "shift_step": g.shift_step,
                    "llm_pattern_model": g.llm_pattern_model,
                    "llm_pattern_model_params": g.llm_pattern_model_params.dict(
                        exclude_none=True) if g.llm_pattern_model_params else None,
                    "llm_pattern_prompt": g.llm_pattern_prompt,
                    "llm_designed_pattern": g.llm_designed_pattern,
                }
                for g in request.games
            ]
        }

        # Only generate join_token if enable_human_test is explicitly set
        join_token = None
        initial_status = 'created'
        if request.enable_human_test:
            join_token = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            initial_status = 'waiting_for_players'

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO test_sets (id, name, description, config, total_games, status, join_token, player_sessions)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                test_set_id, request.name, request.description,
                json.dumps(config_data), total_games, initial_status, join_token,
                json.dumps([])
            )

        logger.info(f"Test set created successfully with ID: {test_set_id}")
        response_data = {
            "test_set_id": test_set_id,
            "message": "Test set created successfully"
        }
        if join_token:
            response_data["join_token"] = join_token
        return response_data

    except Exception as e:
        logger.error(f"Failed to create test set: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create test set: {str(e)}")


@app.get("/api/test-sets", response_model=List[TestSetListResponse])
async def get_test_sets_endpoint():
    """Get test set list."""
    logger.info("Fetching test set list")

    if not db_pool:
        logger.error("Database connection pool not initialized")
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, description, status, total_games, completed_games, created_at, config, join_token FROM test_sets ORDER BY created_at DESC"
            )

            test_sets = []
            for row in rows:
                # Parse config
                config = None
                if row['config']:
                    try:
                        config = json.loads(row['config'])
                    except Exception as e:
                        logger.warning(f"Failed to parse config for test set {row['id']}: {e}")

                test_set = TestSetListResponse(
                    id=row['id'],
                    name=row['name'],
                    description=row['description'],
                    status=row['status'],
                    total_games=row['total_games'],
                    completed_games=row['completed_games'],
                    created_at=row['created_at'],
                    config=config,
                    join_token=row.get('join_token')
                )
                test_sets.append(test_set)

            logger.info(f"Retrieved {len(test_sets)} test sets")
            return test_sets

    except Exception as e:
        logger.error(f"Failed to get test sets: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get test sets: {str(e)}")


@app.get("/api/test-sets/{test_set_id}")
async def get_test_set_details_endpoint(test_set_id: str):
    """Get test set details."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM test_sets WHERE id = $1", test_set_id)
            if not row:
                raise HTTPException(status_code=404, detail="Test set not found")

            config = json.loads(row['config']) if row['config'] else {}

            return {
                'id': row['id'],
                'name': row['name'],
                'description': row['description'],
                'config': config,
                'status': row['status'],
                'total_games': row['total_games'],
                'completed_games': row['completed_games'],
                'created_at': row['created_at'].isoformat(),
                'join_token': row.get('join_token'),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get test set details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get test set details: {str(e)}")


@app.delete("/api/test-sets/{test_set_id}")
async def delete_test_set_endpoint(test_set_id: str):
    """Delete a test set."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            # Delete related game data from game_analytics
            await conn.execute("DELETE FROM game_analytics WHERE test_set_id = $1", test_set_id)

            # Delete from leaderboards
            await conn.execute("DELETE FROM leaderboards WHERE test_set_id = $1", test_set_id)

            # Then delete the test set
            result = await conn.execute("DELETE FROM test_sets WHERE id = $1", test_set_id)

            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Test set not found")

            logger.info(f"Test set {test_set_id} deleted successfully")
            return {"message": "Test set deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete test set: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete test set: {str(e)}")


@app.patch("/api/test-sets/{test_set_id}/status")
async def update_test_set_status_endpoint(test_set_id: str, request: TestSetStatusUpdateRequest):
    """Update test set status."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            if request.completed_games is not None:
                await conn.execute(
                    "UPDATE test_sets SET status = $1, completed_games = $2 WHERE id = $3",
                    request.status, request.completed_games, test_set_id
                )
            else:
                await conn.execute(
                    "UPDATE test_sets SET status = $1 WHERE id = $2",
                    request.status, test_set_id
                )

        return {"message": "Test set status updated successfully"}

    except Exception as e:
        logger.error(f"Failed to update test set status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update test set status: {str(e)}")


@app.get("/api/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard_endpoint(test_set_id: Optional[str] = None, force_recalculate: bool = False):
    """Get leaderboard."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        calculator = LeaderboardCalculator(db_pool)

        # First try to get cached leaderboard data
        if not force_recalculate:
            cached_data = await calculator.get_cached_leaderboard(test_set_id)
            if cached_data:
                logger.info("Using cached leaderboard data")

                test_set_name = None
                if test_set_id:
                    async with db_pool.acquire() as conn:
                        test_set_row = await conn.fetchrow("SELECT name FROM test_sets WHERE id = $1", test_set_id)
                        if test_set_row:
                            test_set_name = test_set_row['name']

                entries = [LeaderboardEntry(**entry) for entry in cached_data]

                return LeaderboardResponse(
                    test_set_id=test_set_id,
                    test_set_name=test_set_name,
                    entries=entries,
                    generated_at=datetime.now()
                )

        # If no cache or force recalculate, compute new leaderboard
        logger.info(f"Computing new leaderboard, force_recalculate={force_recalculate}")
        leaderboard_data = await calculator.calculate_leaderboard(test_set_id)

        test_set_name = None
        if test_set_id:
            async with db_pool.acquire() as conn:
                test_set_row = await conn.fetchrow("SELECT name FROM test_sets WHERE id = $1", test_set_id)
                if test_set_row:
                    test_set_name = test_set_row['name']

        entries = [LeaderboardEntry(**entry) for entry in leaderboard_data]

        return LeaderboardResponse(
            test_set_id=test_set_id,
            test_set_name=test_set_name,
            entries=entries,
            generated_at=datetime.now()
        )

    except Exception as e:
        logger.error(f"Failed to get leaderboard: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get leaderboard: {str(e)}")


@app.post("/api/test-sets/{test_set_id}/start")
async def start_test_set_endpoint(test_set_id: str):
    """Start executing a test set."""
    if not db_pool or not background_tasks_manager:
        raise HTTPException(status_code=500, detail="Service not ready")

    try:
        # Check status and validate for waiting_for_players
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, config, player_sessions FROM test_sets WHERE id = $1", test_set_id)
            if not row:
                raise HTTPException(status_code=404, detail="Test set not found")

            status = row['status']
            if status == 'waiting_for_players':
                config = json.loads(row['config']) if row['config'] else {}
                player_sessions = json.loads(row['player_sessions']) if row['player_sessions'] else []
                participant_count = len(config.get('participants', []))

                if len(player_sessions) < participant_count:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Not all player slots are filled ({len(player_sessions)}/{participant_count})"
                    )

                # Update participant human_name values with actual player names
                participants = config.get('participants', [])
                for session in player_sessions:
                    idx = session.get('participant_index')
                    if idx is not None and idx < len(participants):
                        participants[idx]['human_name'] = session['player_name']
                config['participants'] = participants

                await conn.execute(
                    "UPDATE test_sets SET config = $1, status = 'created' WHERE id = $2",
                    json.dumps(config), test_set_id
                )

        # Start background task
        await background_tasks_manager.start_test_set_execution(test_set_id)

        logger.info(f"Test set {test_set_id} started in background")
        return {"message": "Test set started successfully", "test_set_id": test_set_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start test set: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start test set: {str(e)}")


@app.get("/api/test-sets/{test_set_id}/current-game")
async def get_current_game_state_endpoint(test_set_id: str, player_index: Optional[int] = None):
    """Get current game state (for live viewing).

    If player_index is provided, returns the per-human game state for that participant.
    Otherwise returns the main (LLM) game state.
    """
    with game_states_lock:
        # Try per-human state first if player_index provided
        if player_index is not None:
            human_key = f"{test_set_id}:human:{player_index}"
            if human_key in current_game_states:
                state = current_game_states[human_key]
                if state.get('currentPhase') == 'playing':
                    # Check if THIS player has finished — if so, reveal masterPattern
                    player_finished = False
                    for ps in (state.get('playerStates') or {}).values():
                        if ps.get('type') == 'Human' and ps.get('isFinished'):
                            player_finished = True
                            break
                    if not player_finished:
                        safe_state = {k: v for k, v in state.items() if k != 'masterPattern'}
                        safe_state['masterPattern'] = None
                        return safe_state
                return state

        # Fall back to main test set state (LLM games)
        if test_set_id in current_game_states:
            state = current_game_states[test_set_id]
            # Hide master pattern if game has human players and is still playing
            if state.get('currentPhase') == 'playing':
                has_human = any(
                    ps.get('type') == 'Human'
                    for ps in (state.get('playerStates') or {}).values()
                )
                if has_human:
                    safe_state = {k: v for k, v in state.items() if k != 'masterPattern'}
                    safe_state['masterPattern'] = None
                    return safe_state
            return state

        # Check if any human game states exist for this test set
        human_states = {k: v for k, v in current_game_states.items() if k.startswith(f"{test_set_id}:human:")}
        if human_states:
            # Return the first active human game (for overview purposes)
            for key, state in human_states.items():
                if state.get('currentPhase') == 'playing':
                    safe_state = {k: v for k, v in state.items() if k != 'masterPattern'}
                    safe_state['masterPattern'] = None
                    return safe_state
            # All human games done, return last one
            return list(human_states.values())[-1]

        return {"message": "No active game for this test set"}


@app.post("/api/test-sets/{test_set_id}/human-action")
async def submit_human_action_endpoint(test_set_id: str, request: HumanPlayerActionRequest):
    """Submit a human player's action during test set execution."""
    # Optional session_token validation
    if request.session_token and db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT player_sessions FROM test_sets WHERE id = $1", test_set_id)
            if row and row['player_sessions']:
                player_sessions = json.loads(row['player_sessions'])
                session_valid = any(
                    s.get('session_token') == request.session_token
                    for s in player_sessions
                    if request.player_id in (f"human_{s.get('participant_index')}",
                                              f"Human_{s.get('participant_index')}",
                                              request.player_id)
                )
                if not session_valid:
                    raise HTTPException(status_code=403, detail="Invalid session token for this player")

    # Get game state — check per-human state keys first, then main state
    game_state = None
    state_key = None
    with game_states_lock:
        # Search per-human state keys for this player
        for key, state in current_game_states.items():
            if key.startswith(f"{test_set_id}:human:"):
                if request.player_id in (state.get('playerStates') or {}):
                    game_state = state
                    state_key = key
                    break
        # Fall back to main test set state
        if game_state is None and test_set_id in current_game_states:
            game_state = current_game_states[test_set_id]
            state_key = test_set_id
        if game_state is None:
            raise HTTPException(status_code=404, detail="No active game for this test set")

    # Validate player exists and is human
    player_state = game_state.get('playerStates', {}).get(request.player_id)
    if not player_state:
        raise HTTPException(status_code=404, detail=f"Player {request.player_id} not found")
    if player_state.get('type') != 'Human':
        raise HTTPException(status_code=400, detail="This endpoint is only for human players")
    if player_state.get('isFinished'):
        raise HTTPException(status_code=400, detail="Player has already finished")

    grid_size = game_state['gameConfig']['baseSettings']['gridSize']
    master_pattern = game_state.get('masterPattern')
    if not master_pattern:
        raise HTTPException(status_code=500, detail="Master pattern not available")

    turn_num = player_state['turnNumber']

    if request.action == 'observe':
        if not request.cells_to_observe:
            raise HTTPException(status_code=400, detail="cells_to_observe required for observe action")

        # Limit to 3 cells
        cells = request.cells_to_observe[:3]
        observed_coords = []
        newly_queried = []

        for cell in cells:
            row, col = cell.row, cell.col
            if (0 <= row < grid_size and 0 <= col < grid_size and
                    player_state['grid'][row][col] == '?'):
                player_state['grid'][row][col] = master_pattern[row][col]
                observed_coords.append(f"{chr(65 + col)}{row + 1}")
                newly_queried.append({'row': row, 'col': col})

        player_state['queriedCells'].extend(newly_queried)
        player_state['turnNumber'] += 1

        if observed_coords:
            player_state['log'].append(
                f"Turn {turn_num}: Observed cells: {', '.join(observed_coords)}.")
        else:
            player_state['log'].append(
                f"Turn {turn_num}: No valid cells to observe.")

    elif request.action == 'guess':
        if not request.guess_grid:
            raise HTTPException(status_code=400, detail="guess_grid required for guess action")
        if len(request.guess_grid) != grid_size:
            raise HTTPException(status_code=400, detail=f"guess_grid must be {grid_size}x{grid_size}")

        player_state['finalGuess'] = request.guess_grid
        player_state['isFinished'] = True

        score = calculate_score(
            master_pattern, request.guess_grid, player_state['queriedCells'], grid_size
        )
        player_state['score'] = score
        player_state['log'].append(
            f"Turn {turn_num}: Final guess submitted. Score: {score}.")

    elif request.action == 'give_up':
        player_state['finalGuess'] = [['?' for _ in range(grid_size)] for _ in range(grid_size)]
        player_state['isFinished'] = True
        player_state['score'] = 0
        player_state['log'].append(
            f"Turn {turn_num}: Gave up the game. Final score: 0.")

    # Keep isWaitingForHuman true unless the player has finished (guess/give_up)
    if player_state.get('isFinished'):
        player_state['isWaitingForHuman'] = False

    # Update global state using the correct state key
    with game_states_lock:
        current_game_states[state_key] = game_state

    return {"status": "ok", "player_state": {
        "turnNumber": player_state['turnNumber'],
        "isFinished": player_state['isFinished'],
        "score": player_state['score'],
    }}


@app.get("/api/join/{token}")
async def validate_join_token(token: str):
    """Validate a join token and get test set info."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM test_sets WHERE join_token = $1", token)
        if not row:
            raise HTTPException(status_code=404, detail="Invalid join token")

        config = json.loads(row['config']) if row['config'] else {}
        player_sessions = json.loads(row['player_sessions']) if row['player_sessions'] else []
        participants = config.get('participants', [])
        participant_count = len(participants)

        # Build participant slots with claimed status
        participant_slots = []
        for i, p in enumerate(participants):
            claimed_by = None
            for session in player_sessions:
                if session.get('participant_index') == i:
                    claimed_by = session.get('player_name')
                    break
            participant_slots.append({
                "index": i,
                "claimed": claimed_by is not None,
                "player_name": claimed_by,
            })

        # Count total games
        total_games = row['total_games'] or 0

        return {
            "test_set_id": row['id'],
            "name": row['name'],
            "description": row['description'],
            "status": row['status'],
            "participant_slots": participant_slots,
            "total_games": total_games,
            "participant_count": participant_count,
        }


@app.post("/api/join/{token}", response_model=JoinResponse)
async def join_test_set(token: str, request: JoinRequest):
    """Claim a player slot in a human test set."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM test_sets WHERE join_token = $1", token)
        if not row:
            raise HTTPException(status_code=404, detail="Invalid join token")

        config = json.loads(row['config']) if row['config'] else {}
        player_sessions = json.loads(row['player_sessions']) if row['player_sessions'] else []
        participants = config.get('participants', [])
        participant_count = len(participants)

        # Check name not already taken (case-insensitive)
        for session in player_sessions:
            if session.get('player_name', '').lower() == request.player_name.lower():
                raise HTTPException(status_code=409, detail="Name already taken")

        # Find first unclaimed participant index
        claimed_indices = {s.get('participant_index') for s in player_sessions}
        available_index = None
        for i in range(participant_count):
            if i not in claimed_indices:
                available_index = i
                break

        if available_index is None:
            raise HTTPException(status_code=409, detail="All player slots are full")

        # Generate session token
        session_token = str(uuid.uuid4())

        # Build new session entry
        new_session = {
            "participant_index": available_index,
            "player_name": request.player_name,
            "session_token": session_token,
            "joined_at": datetime.now().isoformat(),
        }
        player_sessions.append(new_session)

        # Update the participant's human_name in config
        if available_index < len(participants):
            participants[available_index]['human_name'] = request.player_name
            config['participants'] = participants

        # Check if all slots are now filled
        all_slots_filled = len(player_sessions) >= participant_count

        # Atomically update player_sessions, config, and status if auto-starting
        if all_slots_filled and row['status'] == 'waiting_for_players':
            await conn.execute(
                "UPDATE test_sets SET player_sessions = $1, config = $2, status = 'created' WHERE id = $3",
                json.dumps(player_sessions), json.dumps(config), row['id']
            )
        else:
            await conn.execute(
                "UPDATE test_sets SET player_sessions = $1, config = $2 WHERE id = $3",
                json.dumps(player_sessions), json.dumps(config), row['id']
            )

        test_set_id = row['id']

        # Auto-start the test when all players have joined
        if all_slots_filled and row['status'] == 'waiting_for_players':
            try:
                if background_tasks_manager:
                    logger.info(f"All players joined for test set {test_set_id}, auto-starting...")
                    await background_tasks_manager.start_test_set_execution(test_set_id)
            except Exception as e:
                logger.error(f"Failed to auto-start test set {test_set_id}: {e}")

        return JoinResponse(
            session_token=session_token,
            participant_index=available_index,
            test_set_id=test_set_id,
            player_name=request.player_name,
        )


@app.get("/api/join/{token}/session/{session_token}")
async def resume_session(token: str, session_token: str):
    """Resume an existing player session."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM test_sets WHERE join_token = $1", token)
        if not row:
            raise HTTPException(status_code=404, detail="Invalid join token")

        player_sessions = json.loads(row['player_sessions']) if row['player_sessions'] else []

        # Find matching session
        session_entry = None
        for s in player_sessions:
            if s.get('session_token') == session_token:
                session_entry = s
                break

        if not session_entry:
            raise HTTPException(status_code=404, detail="Session not found")

        participant_index = session_entry['participant_index']
        test_set_id = row['id']

        # Get current game state for this player
        game_state = None
        with game_states_lock:
            human_key = f"{test_set_id}:human:{participant_index}"
            if human_key in current_game_states:
                state = current_game_states[human_key]
                # Hide master pattern if still playing
                if state.get('currentPhase') == 'playing':
                    game_state = {k: v for k, v in state.items() if k != 'masterPattern'}
                    game_state['masterPattern'] = None
                else:
                    game_state = state

        return {
            "participant_index": participant_index,
            "player_name": session_entry['player_name'],
            "test_set_id": test_set_id,
            "status": row['status'],
            "game_state": game_state,
        }


@app.get("/api/test-sets/{test_set_id}/scoreboard", response_model=ScoreboardResponse)
async def get_scoreboard(test_set_id: str):
    """Get live scoreboard for a test set."""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT config, player_sessions FROM test_sets WHERE id = $1", test_set_id)
        if not row:
            raise HTTPException(status_code=404, detail="Test set not found")

        config = json.loads(row['config']) if row['config'] else {}
        player_sessions = json.loads(row['player_sessions']) if row['player_sessions'] else []
        participants = config.get('participants', [])

        # Count total games per human
        total_games_per_human = 0
        for g in config.get('games', []):
            total_games_per_human += g.get('repeat_count', 1)

        # Get completed game scores from DB
        completed_scores = await conn.fetch(
            """
            SELECT participant_id, participant_name, final_score
            FROM game_analytics
            WHERE test_set_id = $1 AND participant_type = 'Human' AND participant_role = 'player'
            ORDER BY created_at ASC
            """,
            test_set_id
        )

        # Group completed scores by participant name
        scores_by_name: Dict[str, list] = defaultdict(list)
        for sr in completed_scores:
            scores_by_name[sr['participant_name']].append(sr['final_score'])

        players = []
        for session in player_sessions:
            p_idx = session['participant_index']
            p_name = session['player_name']

            # Completed games from DB
            completed = scores_by_name.get(p_name, [])
            cumulative = sum(completed)
            completed_count = len(completed)

            # Check in-memory active game state
            current_game_index = completed_count
            is_finished = completed_count >= total_games_per_human

            with game_states_lock:
                human_key = f"{test_set_id}:human:{p_idx}"
                if human_key in current_game_states:
                    state = current_game_states[human_key]
                    # If active game has a score (player finished this game)
                    player_states = state.get('playerStates', {})
                    for pid, ps in player_states.items():
                        if ps.get('type') == 'Human' and ps.get('isFinished'):
                            # This score may not yet be saved to DB
                            pass

            players.append(ScoreboardEntry(
                name=p_name,
                participant_index=p_idx,
                current_game_index=current_game_index,
                total_games=total_games_per_human,
                cumulative_score=cumulative,
                is_finished=is_finished,
            ))

        # Sort by cumulative_score descending
        players.sort(key=lambda x: x.cumulative_score, reverse=True)

        return ScoreboardResponse(players=players)


@app.post("/api/test-sets/{test_set_id}/human-ready")
async def signal_human_ready(test_set_id: str, request: dict):
    """Signal that a human player is ready for the next game."""
    session_token = request.get('session_token')
    participant_index = request.get('participant_index')

    if session_token is None or participant_index is None:
        raise HTTPException(status_code=400, detail="session_token and participant_index required")

    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    # Validate session_token
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT player_sessions FROM test_sets WHERE id = $1", test_set_id)
        if not row:
            raise HTTPException(status_code=404, detail="Test set not found")

        player_sessions = json.loads(row['player_sessions']) if row['player_sessions'] else []
        valid = False
        for s in player_sessions:
            if s.get('session_token') == session_token and s.get('participant_index') == participant_index:
                valid = True
                break
        if not valid:
            raise HTTPException(status_code=403, detail="Invalid session for this participant")

    # Set ready_for_next flag in the in-memory game state
    human_key = f"{test_set_id}:human:{participant_index}"
    with game_states_lock:
        if human_key in current_game_states:
            current_game_states[human_key]['ready_for_next'] = True

    return {"status": "ok"}


@app.get("/health")
async def health_check():
    """Health check."""
    db_status = "connected" if db_pool else "disconnected"
    openrouter_status = "configured" if openrouter_client else "not configured"
    return {
        "status": "healthy",
        "openrouter_configured": bool(openrouter_client),
        "database_connected": bool(db_pool),
        "database_status": db_status,
        "openrouter_status": openrouter_status,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
