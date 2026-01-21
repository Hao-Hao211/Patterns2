import os
import uuid
import json
import logging
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Dict, Any, Union, Set, Tuple
import asyncpg
from contextlib import asynccontextmanager
import httpx
from dotenv import load_dotenv
import threading
from collections import defaultdict
import openai
import numpy as np

# 导入排行榜模块
try:
    from leaderboard import LeaderboardCalculator, create_leaderboard_tables
except ImportError:
    # 如果排行榜模块不存在，创建占位符
    class LeaderboardCalculator:
        def __init__(self, db_pool):
            self.db_pool = db_pool

        async def calculate_leaderboard(self, test_set_id=None):
            return []

        async def get_cached_leaderboard(self, test_set_id=None):
            return None

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 获取API密钥
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 全局OpenAI模型列表缓存
openai_models_cache: Set[str] = set()
openai_models_cache_lock = threading.Lock()

# 初始化OpenRouter客户端
openrouter_client = None
if OPENROUTER_API_KEY:
    try:
        openrouter_client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=60.0
        )
        logger.info("OpenRouter客户端初始化成功")
    except Exception as e:
        logger.error(f"OpenRouter客户端初始化失败: {e}")
        openrouter_client = None
else:
    logger.warning("OPENROUTER_API_KEY环境变量未设置")

# 初始化OpenAI客户端
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import AsyncOpenAI

        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        logger.info("OpenAI客户端初始化成功")
    except Exception as e:
        logger.error(f"OpenAI客户端初始化失败: {e}")
        openai_client = None
else:
    logger.warning("OPENAI_API_KEY环境变量未设置")

# 全局chat history存储 - 线程安全
chat_histories_lock = threading.Lock()
chat_histories: Dict[str, List[Dict[str, str]]] = defaultdict(list)

# 全局后台任务管理器
background_tasks_manager = None

# 全局游戏状态存储 - 用于实时观看
game_states_lock = threading.Lock()
current_game_states: Dict[str, Dict[str, Any]] = {}

# 全局token使用跟踪 - 用于跟踪每个玩家的token使用情况
player_token_usage_lock = threading.Lock()
player_token_usage: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0})


class BackgroundTasksManager:
    """后台任务管理器"""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.task_lock = asyncio.Lock()

    async def start_test_set_execution(self, test_set_id: str):
        """启动测试集后台执行"""
        async with self.task_lock:
            if test_set_id in self.running_tasks:
                logger.info(f"Test set {test_set_id} is already running")
                return

            # 检查测试集状态，如果是 pending，先更新为 created
            async with self.db_pool.acquire() as conn:
                test_set_row = await conn.fetchrow("SELECT status FROM test_sets WHERE id = $1", test_set_id)
                if test_set_row and test_set_row['status'] == 'pending':
                    await conn.execute(
                        "UPDATE test_sets SET status = 'created' WHERE id = $1",
                        test_set_id
                    )
                    logger.info(f"Updated test set {test_set_id} status from pending to created")

            # 创建后台任务
            task = asyncio.create_task(self._execute_test_set(test_set_id))
            self.running_tasks[test_set_id] = task
            logger.info(f"Started background execution for test set {test_set_id}")

    async def _execute_test_set(self, test_set_id: str):
        """执行测试集的核心逻辑"""
        try:
            logger.info(f"开始执行测试集 {test_set_id}")

            # 获取测试集配置
            async with self.db_pool.acquire() as conn:
                test_set_row = await conn.fetchrow("SELECT * FROM test_sets WHERE id = $1", test_set_id)
                if not test_set_row:
                    logger.error(f"Test set {test_set_id} not found")
                    return

                config = json.loads(test_set_row['config'])

                # 更新状态为运行中
                await conn.execute(
                    "UPDATE test_sets SET status = 'running' WHERE id = $1",
                    test_set_id
                )

            # 生成游戏配置
            game_configs = self._generate_game_configs(config)
            total_games = len(game_configs)

            logger.info(f"测试集 {test_set_id} 总共需要执行 {total_games} 局游戏")

            # 逐个执行游戏
            for game_index, game_config in enumerate(game_configs):
                try:
                    logger.info(f"执行第 {game_index + 1}/{total_games} 局游戏")

                    # 生成主模式 - 使用现有的design pattern API
                    master_pattern = await self._generate_master_pattern_via_api(game_config)

                    # 执行游戏（带实时状态更新）- 使用现有的LLM player API
                    game_result = await self._execute_single_game_with_states(
                        test_set_id, game_index, game_config, master_pattern
                    )

                    # 保存游戏结果 - 使用现有的save game API
                    await self._save_game_result_via_api(test_set_id, game_config, master_pattern, game_result)

                    # 更新进度
                    completed_games = game_index + 1
                    async with self.db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE test_sets SET completed_games = $1 WHERE id = $2",
                            completed_games, test_set_id
                        )

                    logger.info(f"完成第 {completed_games}/{total_games} 局游戏")

                    # 清理当前游戏状态
                    with game_states_lock:
                        if test_set_id in current_game_states:
                            del current_game_states[test_set_id]

                    # 短暂延迟避免API限制
                    await asyncio.sleep(2)

                except Exception as e:
                    logger.error(f"执行第 {game_index + 1} 局游戏失败: {e}")
                    continue

            # 标记为完成
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE test_sets SET status = 'completed', completed_games = $1 WHERE id = $2",
                    total_games, test_set_id
                )

            logger.info(f"测试集 {test_set_id} 执行完成")

            # 检查是否有下一个待执行的测试集
            await self._check_and_start_next_test_set()

        except Exception as e:
            logger.error(f"测试集 {test_set_id} 执行失败: {e}")
            # 标记为失败
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE test_sets SET status = 'failed' WHERE id = $1",
                    test_set_id
                )
        finally:
            # 清理任务
            async with self.task_lock:
                if test_set_id in self.running_tasks:
                    del self.running_tasks[test_set_id]

            # 清理游戏状态
            with game_states_lock:
                if test_set_id in current_game_states:
                    del current_game_states[test_set_id]

    async def _check_and_start_next_test_set(self):
        """检查并启动下一个待执行的测试集"""
        try:
            async with self.db_pool.acquire() as conn:
                next_test_set = await conn.fetchrow(
                    "SELECT id FROM test_sets WHERE status IN ('created', 'pending') ORDER BY created_at ASC LIMIT 1"
                )

            if next_test_set:
                logger.info(f"发现下一个待执行的测试集: {next_test_set['id']}")
                await self.start_test_set_execution(next_test_set['id'])
        except Exception as e:
            logger.error(f"检查下一个测试集失败: {e}")

    def _generate_game_configs(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成游戏配置列表"""
        game_configs = []

        for game_template in config['games']:
            for repeat in range(game_template['repeat_count']):
                if config['llm_rotate_designer']:
                    # 每个参与者轮流当设计师
                    for designer_index, designer in enumerate(config['participants']):
                        # 创建玩家列表，排除当前设计师
                        players_for_this_game = []
                        for i, p in enumerate(config['participants']):
                            if i != designer_index:  # 排除当前设计师
                                players_for_this_game.append({
                                    'id': f'player-{i}',
                                    'name': f"{p['model_name']}{'-custom' if p.get('model_params') else ''}",
                                    'type': 'LLM',
                                    'llmModel': p['model_name'],
                                    'llmModelParams': p.get('model_params'),
                                })
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
                            },
                            'players': players_for_this_game

                        }
                        game_configs.append(game_config)
                else:
                    # 使用自定义模式或随机模式
                    game_config = {
                        'baseSettings': {
                            'gridSize': game_template['grid_size'],
                            'numSymbols': game_template['num_symbols'],
                        },
                        'designer': {
                            'type': 'Human',
                            'patternMode': 'Custom' if game_template.get('custom_pattern') else 'Random',
                            'customPattern': game_template.get('custom_pattern'),
                        },
                        'players': [
                            {
                                'id': f'player-{i}',
                                'name': f"{p['model_name']}{'-custom' if p.get('model_params') else ''}",
                                'type': 'LLM',
                                'llmModel': p['model_name'],
                                'llmModelParams': p.get('model_params'),
                            }
                            for i, p in enumerate(config['participants'])
                        ]
                    }
                    game_configs.append(game_config)

        return game_configs

    async def _generate_master_pattern_via_api(self, game_config: Dict[str, Any]) -> List[List[str]]:
        """使用现有的design pattern API生成主模式"""

        # 首先检查是否有自定义模式
        if (game_config['designer']['type'] == 'Human' and
                game_config['designer'].get('patternMode') == 'Custom' and
                game_config['designer'].get('customPattern')):

            custom_pattern = game_config['designer']['customPattern']
            logger.info(f"使用自定义模式: {custom_pattern}")

            # 验证自定义模式的格式和大小
            grid_size = game_config['baseSettings']['gridSize']
            if (isinstance(custom_pattern, list) and
                    len(custom_pattern) == grid_size and
                    all(isinstance(row, list) and len(row) == grid_size for row in custom_pattern)):

                logger.info(f"自定义模式验证通过，使用提供的模式")
                return custom_pattern
            else:
                logger.warning(f"自定义模式格式不正确，回退到随机模式")

        # LLM设计师模式
        if game_config['designer']['type'] == 'LLM':
            try:
                # 构建DesignPatternRequest
                request_data = {
                    'gridSize': game_config['baseSettings']['gridSize'],
                    'numSymbols': game_config['baseSettings']['numSymbols'],
                    'llmModel': game_config['designer']['llmModel'],
                    'llmModelParams': game_config['designer'].get('llmModelParams'),
                    'prompt': game_config['designer'].get('llmPrompt')
                }

                # 直接调用design pattern端点的逻辑（带重试机制）
                design_request = DesignPatternRequest(**request_data)
                design_response = await design_pattern_logic_with_retry(design_request)
                logger.info(f"LLM设计师生成模式成功")
                return design_response.pattern

            except Exception as e:
                logger.error(f"LLM模式生成失败: {e}")

        # 回退到随机模式
        logger.info(f"使用随机模式生成")
        grid_size = game_config['baseSettings']['gridSize']
        num_symbols = game_config['baseSettings']['numSymbols']
        symbols = ALL_SYMBOLS_PY[:num_symbols]

        import random
        pattern = []
        for _ in range(grid_size):
            row = [random.choice(symbols) for _ in range(grid_size)]
            pattern.append(row)

        return pattern

    async def _execute_single_game_with_states(self, test_set_id: str, game_index: int,
                                               game_config: Dict[str, Any], master_pattern: List[List[str]]) -> Dict[
        str, Any]:
        """执行单局游戏并更新实时状态 - 使用现有的LLM player API"""
        game_id = str(uuid.uuid4())

        # 初始化游戏状态
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

        # 初始化玩家状态
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
                'inputTokens': 0,  # 添加token跟踪
                'outputTokens': 0
            }

        # 存储到全局状态
        with game_states_lock:
            current_game_states[test_set_id] = game_state

        # 执行游戏 - 使用现有的LLM player API
        symbols_in_use = ALL_SYMBOLS_PY[:game_config['baseSettings']['numSymbols']]
        turn = 0

        while True:
            all_finished = True
            turn += 1
            game_state['currentTurn'] = turn

            for player in game_config['players']:
                player_state = game_state['playerStates'][player['id']]

                if player_state['finalGuess'] is not None:
                    continue  # 玩家已完成

                all_finished = False
                player_state['isWaitingForLLM'] = True

                # 更新全局状态
                with game_states_lock:
                    current_game_states[test_set_id] = game_state

                try:
                    # 使用现有的LLM player turn API（带重试机制）
                    llm_request_data = {
                        'playerId': player['id'],
                        'playerName': player['name'],
                        'gameId': game_id,
                        'gridSize': game_config['baseSettings']['gridSize'],
                        'symbolsInUse': symbols_in_use,
                        'currentGrid': player_state['grid'],
                        'llmModel': player.get('llmModel', 'openai_official/chatgpt-4o-latest'),
                        'llmModelParams': player.get('llmModelParams'),
                        'turnNumber': player_state['turnNumber']
                    }

                    llm_request = LLMPlayerTurnRequest(**llm_request_data)
                    llm_response, actual_input_tokens, actual_output_tokens = await llm_player_turn_logic_with_retry(
                        llm_request)

                    player_state['isWaitingForLLM'] = False
                    player_state['turnNumber'] += 1

                    # 使用实际的token使用量
                    player_state['inputTokens'] += actual_input_tokens
                    player_state['outputTokens'] += actual_output_tokens

                    # 处理LLM响应
                    if llm_response.action == 'observe':
                        if llm_response.cellsToObserve:
                            observed_coords = []
                            newly_queried = []

                            for cell in llm_response.cellsToObserve[:3]:  # 最多3个
                                row, col = cell.row, cell.col
                                if (0 <= row < game_config['baseSettings']['gridSize'] and
                                        0 <= col < game_config['baseSettings']['gridSize'] and
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
                        if llm_response.guessGrid and len(llm_response.guessGrid) == game_config['baseSettings'][
                            'gridSize']:
                            player_state['finalGuess'] = llm_response.guessGrid
                            player_state['isFinished'] = True

                            # 计算分数
                            score = calculate_score(
                                master_pattern, llm_response.guessGrid, player_state['queriedCells'],
                                game_config['baseSettings']['gridSize']
                            )
                            player_state['score'] = score

                            confidence_text = f" (Confidence: {llm_response.confidence * 100:.1f}%)" if llm_response.confidence else ""
                            player_state['log'].append(
                                f"Turn {player_state['turnNumber'] - 1}: Final guess submitted{confidence_text}. Score: {score}. {llm_response.reasoning}")

                    else:  # give_up
                        player_state['finalGuess'] = [['?' for _ in range(game_config['baseSettings']['gridSize'])]
                                                      for _ in range(game_config['baseSettings']['gridSize'])]
                        player_state['isFinished'] = True
                        player_state['score'] = 0
                        player_state['log'].append(
                            f"Turn {player_state['turnNumber'] - 1}: Gave up the game. Final score: 0. {llm_response.reasoning}")

                except Exception as e:
                    logger.error(f"玩家 {player['name']} 第 {player_state['turnNumber']} 轮执行失败: {e}")
                    # 强制结束该玩家
                    player_state['isWaitingForLLM'] = False
                    player_state['finalGuess'] = [['?' for _ in range(game_config['baseSettings']['gridSize'])]
                                                  for _ in range(game_config['baseSettings']['gridSize'])]
                    player_state['isFinished'] = True
                    player_state['score'] = 0
                    player_state['log'].append(
                        f"Turn {player_state['turnNumber'] - 1}: Error occurred after maximum retries, game ended. {str(e)}")

                # 更新全局状态
                with game_states_lock:
                    current_game_states[test_set_id] = game_state

                # 短暂延迟
                await asyncio.sleep(1)

            # 检查是否所有玩家都完成了
            if all_finished:
                game_state['allPlayersFinished'] = True
                game_state['currentPhase'] = 'results'
                with game_states_lock:
                    current_game_states[test_set_id] = game_state
                break

        # 计算最终分数
        player_scores = []
        for player in game_config['players']:
            player_state = game_state['playerStates'][player['id']]
            if player_state['score'] is None:
                # 如果没有分数，计算一个默认分数
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

        # 等待一段时间让用户看到结果
        await asyncio.sleep(3)

        return {
            'playerStates': {pid: pstate for pid, pstate in game_state['playerStates'].items()},
            'playerScores': player_scores
        }

    async def _save_game_result_via_api(self, test_set_id: str, game_config: Dict[str, Any],
                                        master_pattern: List[List[str]], game_result: Dict[str, Any]):
        """使用现有的save game API保存游戏结果"""
        try:
            # 构建GameCreateRequest
            players_data = []
            for player in game_config['players']:
                player_state = game_result['playerStates'][player['id']]
                player_score = next((ps for ps in game_result['playerScores'] if ps['playerId'] == player['id']),
                                    {'score': 0})

                # 转换queried_cells格式
                queried_cells = []
                for cell in player_state.get('queriedCells', []):
                    queried_cells.append(PositionModel(row=cell['row'], col=cell['col']))

                player_data = PlayerStateInGame(
                    player_name_in_game=player['name'],
                    player_type=player['type'],
                    player_llm_model=player['llmModel'],
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

            # 直接调用save game端点的逻辑
            await save_game_logic(game_request)

        except Exception as e:
            logger.error(f"保存游戏结果失败: {e}")


# --- Helper Functions ---

def analyze_action_log(action_log: Optional[List[str]]) -> tuple[int, int, int]:
    """
    分析action_log，提取观察次数、观察轮数和是否退出

    Returns:
        tuple: (observation_count, observation_rounds, did_quit)
    """
    if not action_log:
        return 0, 0, 0

    observation_count = 0
    observation_rounds = 0
    did_quit = 0

    for log_entry in action_log:
        # 检查是否包含观察行为
        if "Observed cells:" in log_entry:
            observation_rounds += 1
            # 提取观察的单元格数量
            # 格式: "Turn X: Observed cells: A1, B2, C3. ..."
            try:
                cells_part = log_entry.split("Observed cells:")[1].split(".")[0]
                cells = [c.strip() for c in cells_part.split(",") if c.strip()]
                observation_count += len(cells)
            except:
                pass

        # 检查是否退出游戏
        if "Gave up the game" in log_entry or "give_up" in log_entry.lower():
            did_quit = 1

    return observation_count, observation_rounds, did_quit


def calculate_ranks(scores: List[tuple[str, int]]) -> dict[str, int]:
    """
    根据分数计算排名（分数高的排名靠前，相同分数排名相同）

    Args:
        scores: List of (participant_id, score) tuples

    Returns:
        dict: {participant_id: rank}
    """
    # 按分数降序排序
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


def calculate_score(master_pattern: List[List[str]], guess: List[List[str]],
                    queried_cells: List[Dict[str, int]], grid_size: int) -> int:
    """计算玩家分数"""
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


def designer_percentile(scores: List[float], grid_size: Tuple[int, int],
                        a: float = 7.0, b: float = 3.0, m0: float = 0.40,
                        s0: float = 0.30, sigma_ref: float = 4.0) -> float:
    """
    Calculate designer percentile based on player scores

    Args:
        scores: List of player scores
        grid_size: (height, width) of the grid
        a, b: Difficulty and discrimination weights
        m0, s0: Target mean and std thresholds
        sigma_ref: Reference sigma for competitiveness compression

    Returns:
        p_prime: Designer percentile (0 to 1)
    """
    H, W = grid_size
    Smax = H * W
    scores_array = np.asarray(scores, dtype=float)
    mu, sigma = scores_array.mean(), scores_array.std()

    # Normalize mean and std
    m = mu / Smax
    s = min(sigma, Smax / 2) / (Smax / 2)

    # Step A: Difficulty & Discrimination
    z = a * (m0 - m) + b * (s - s0)
    p = 1.0 / (1.0 + np.exp(-z))

    # Step B: Competitiveness Compression
    kappa = min(1.0, sigma / sigma_ref)
    p_prime = 0.5 + kappa * (p - 0.5)

    return float(p_prime)


def designer_in_game_score(scores: List[float], p_prime: float,
                           bump_top: bool = False, eps: float = 1e-6) -> float:
    """
    Calculate in-game designer score that can be ranked with player scores

    Args:
        scores: List of player scores
        p_prime: Designer percentile
        bump_top: If True, add epsilon when at top percentile
        eps: Small value to add when bumping top

    Returns:
        Designer score that can be compared with player scores
    """
    scores_array = np.sort(np.asarray(scores, dtype=float))
    n = len(scores_array)
    pis = (np.arange(1, n + 1) - 0.5) / n
    val = float(np.interp(p_prime, pis, scores_array))

    if bump_top and p_prime >= pis[-1]:
        val = scores_array[-1] + eps

    return val


def designer_meta_score(p_prime: float, grid_size: Tuple[int, int]) -> float:
    """
    Calculate meta designer score for cross-game comparison

    Args:
        p_prime: Designer percentile
        grid_size: (height, width) of the grid

    Returns:
        Meta score in range [0, Smax]
    """
    H, W = grid_size
    return (H * W) * p_prime


def calculate_designer_scores(player_scores: List[float], grid_size: int,
                              num_symbols: int) -> Tuple[float, float]:
    """
    Calculate both in-game and meta designer scores using the new algorithm

    Args:
        player_scores: List of player scores
        grid_size: Grid size (assuming square grid)
        num_symbols: Number of symbols (not used in calculation but kept for compatibility)

    Returns:
        (in_game_designer_score, meta_designer_score)
    """
    if not player_scores or len(player_scores) < 1:
        return 0.0, 0.0

    # Assume square grid for now (can be extended to rectangular)
    grid_tuple = (grid_size, grid_size)

    # Calculate percentile
    p_prime = designer_percentile(player_scores, grid_tuple)

    # Calculate in-game score (for ranking)
    in_game_score = designer_in_game_score(player_scores, p_prime, bump_top=True)

    # Calculate meta score (for cross-game comparison)
    meta_score = designer_meta_score(p_prime, grid_tuple)

    return in_game_score, meta_score


# --- 全局异常处理器 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时创建数据库连接池
    global db_pool, background_tasks_manager
    try:
        DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/patterns_db")
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("数据库连接池创建成功")

        # 自动创建表（如果不存在）
        await create_tables_if_not_exist()

        # 创建排行榜相关表
        await create_leaderboard_tables(db_pool)

        # 初始化OpenAI模型缓存
        await initialize_openai_models_cache()

        # 初始化后台任务管理器
        background_tasks_manager = BackgroundTasksManager(db_pool)

        # 启动时检查是否有待执行的测试集
        await background_tasks_manager._check_and_start_next_test_set()

    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        db_pool = None

    yield

    # 关闭时清理连接池
    if db_pool:
        await db_pool.close()
        logger.info("数据库连接池已关闭")

    # 关闭OpenRouter客户端
    if openrouter_client:
        await openrouter_client.aclose()
        logger.info("OpenRouter客户端已关闭")


async def initialize_openai_models_cache():
    """初始化OpenAI官方模型缓存"""
    global openai_models_cache

    if not openai_client:
        logger.info("OpenAI客户端未配置，使用默认模型列表")
        with openai_models_cache_lock:
            openai_models_cache.update([
                'chatgpt-4o-latest',
                'gpt-4o',
                'gpt-4o-mini',
                'gpt-4-turbo',
                'gpt-3.5-turbo',
                'o1-preview',
                'o1-mini',
                'gpt-4',
                'gpt-4-turbo-preview',
                'gpt-4-0125-preview',
                'gpt-4-1106-preview',
                'gpt-3.5-turbo-0125',
                'gpt-3.5-turbo-1106',
                'o1'
            ])
        return

    try:
        logger.info("正在从OpenAI官方API获取模型列表以初始化缓存...")
        models_response = await openai_client.models.list()

        # 需要排除的关键词
        exclude_keywords = ["embedding", "whisper", "tts", "dall-e"]

        openai_model_ids = set()
        for model in models_response.data:
            model_id = model.id.lower()
            # 跳过包含排除关键词的模型
            if any(keyword in model_id for keyword in exclude_keywords):
                continue
            openai_model_ids.add(model.id)  # 保持原始大小写，不添加前缀

        with openai_models_cache_lock:
            openai_models_cache.clear()
            openai_models_cache.update(openai_model_ids)

        logger.info(f"成功缓存了 {len(openai_model_ids)} 个OpenAI官方模型")

    except Exception as e:
        logger.error(f"获取OpenAI官方模型列表失败，使用默认列表: {e}")
        # 如果API调用失败，使用默认模型列表
        with openai_models_cache_lock:
            openai_models_cache.update([
                'chatgpt-4o-latest',
                'gpt-4o',
                'gpt-4o-mini',
                'gpt-4-turbo',
                'gpt-3.5-turbo',
                'o1-preview',
                'o1-mini',
                'gpt-4',
                'gpt-4-turbo-preview',
                'gpt-4-0125-preview',
                'gpt-4-1106-preview',
                'gpt-3.5-turbo-0125',
                'gpt-3.5-turbo-1106',
                'o1'
            ])


async def create_tables_if_not_exist():
    """创建数据库表（如果不存在）"""
    if not db_pool:
        logger.warning("数据库连接池未初始化，跳过表创建")
        return

    try:
        async with db_pool.acquire() as conn:
            # Check if games table exists
            result = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'games')"
            )

            if not result:
                logger.info("创建数据库表...")

                # Create games table
                await conn.execute("""
                                   CREATE TABLE games
                                   (
                                       id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                                       created_at                TIMESTAMPTZ      DEFAULT NOW(),
                                       grid_size                 INTEGER NOT NULL,
                                       num_symbols               INTEGER NOT NULL,
                                       designer_type             TEXT    NOT NULL,
                                       designer_llm_model        TEXT,
                                       designer_llm_model_params JSONB,
                                       designer_pattern_mode     TEXT,
                                       master_pattern            JSONB   NOT NULL,
                                       game_config_dump          JSONB,
                                       test_set_id               TEXT
                                   )
                                   """)

                # Create game_players table
                await conn.execute("""
                                   CREATE TABLE game_players
                                   (
                                       id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                                       game_id                 UUID NOT NULL REFERENCES games (id) ON DELETE CASCADE,
                                       player_name_in_game     TEXT NOT NULL,
                                       player_type             TEXT NOT NULL,
                                       player_llm_model        TEXT,
                                       player_llm_model_params JSONB,
                                       final_score             INTEGER,
                                       final_guess             JSONB,
                                       action_log              JSONB,
                                       queried_cells           JSONB,
                                       input_tokens            INTEGER          DEFAULT 0,
                                       output_tokens           INTEGER          DEFAULT 0
                                   )
                                   """)

                logger.info("基础表创建成功")

            # Check if game_analytics table exists
            analytics_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'game_analytics')"
            )

            if not analytics_exists:
                logger.info("创建 game_analytics 表...")
                await conn.execute("""
                                   CREATE TABLE game_analytics
                                   (
                                       id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                                       game_id                      UUID    NOT NULL REFERENCES games (id) ON DELETE CASCADE,
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

                                       created_at                   TIMESTAMPTZ      DEFAULT NOW()
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

                logger.info("game_analytics 表创建成功")
            else:
                # Check if in_game_designer_score column exists
                column_exists = await conn.fetchval("""
                                                    SELECT EXISTS (SELECT
                                                                   FROM information_schema.columns
                                                                   WHERE table_name = 'game_analytics'
                                                                     AND column_name = 'in_game_designer_score')
                                                    """)

                if not column_exists:
                    logger.info("添加 in_game_designer_score 和 meta_designer_score 列到 game_analytics 表...")
                    await conn.execute("""
                                       ALTER TABLE game_analytics
                                           ADD COLUMN in_game_designer_score DECIMAL(10, 2) DEFAULT 0.0,
                                           ADD COLUMN meta_designer_score    DECIMAL(10, 2) DEFAULT 0.0
                                       """)
                    logger.info("新设计师分数列添加成功")

            # Ensure indexes exist (idempotent)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_players_game_id ON game_players(game_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_created_at ON games(created_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_test_set_id ON games(test_set_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_created_at ON test_sets(created_at DESC)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_status ON test_sets(status)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_game_players_tokens ON game_players(input_tokens, output_tokens)")

            logger.info("数据库表已存在或已更新")

    except Exception as e:
        logger.error(f"创建数据库表失败: {e}")


# --- FastAPI应用初始化 ---
app = FastAPI(title="Patterns II Game Backend", lifespan=lifespan, redirect_slashes=False)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    捕获并格式化Pydantic验证错误，确保返回统一的JSON格式。
    """
    error_messages = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        message = error["msg"]
        error_messages.append(f"Field '{field}': {message}")

    detail = "; ".join(error_messages)
    logger.error(f"请求验证失败: {detail}")

    return JSONResponse(
        status_code=422,
        content={"detail": detail},
    )


# --- 数据模型定义 ---
Symbol = Literal["+", "○", "△", "□", "★", "✖"]
Cell = Optional[Symbol]
Grid = List[List[Cell]]

ALL_SYMBOLS_PY: List[Symbol] = ["○", "△", "✖", "□", "★", "+"]


class PositionModel(BaseModel):
    row: int
    col: int


class OpenRouterModel(BaseModel):
    id: str
    name: str
    created: int = 0
    description: Optional[str] = None
    architecture: Optional[Dict[str, Any]] = None
    top_provider: Optional[Dict[str, Any]] = None
    pricing: Optional[Dict[str, Any]] = None
    canonical_slug: Optional[str] = None
    context_length: Optional[int] = None
    hugging_face_id: Optional[str] = None
    per_request_limits: Optional[Dict[str, Any]] = None
    supported_parameters: Optional[List[str]] = None


class ModelsListResponse(BaseModel):
    object: str = "list"
    data: List[OpenRouterModel]


# 新增：LLM模型参数配置
class LLMModelParams(BaseModel):
    model_config = {"protected_namespaces": ()}

    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    maxCompletionTokens: Optional[int] = Field(None, ge=1, le=4096)
    topP: Optional[float] = Field(None, ge=0.0, le=1.0)
    frequencyPenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    presencePenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)


# 修复后的LLM Player API模型
class LLMPlayerTurnRequest(BaseModel):
    """LLM玩家单次行动请求"""
    playerId: str
    playerName: str
    gameId: str
    gridSize: int
    symbolsInUse: List[Symbol]
    currentGrid: List[List[str]]
    llmModel: Optional[str] = "openai_official/chatgpt-4o-latest"
    llmModelParams: Optional[LLMModelParams] = None
    turnNumber: int = 1

    @field_validator('currentGrid')
    @classmethod
    def validate_grid(cls, v, info):
        """验证网格格式"""
        values = info.data
        grid_size = values.get('gridSize')
        if grid_size is None:
            raise ValueError("gridSize must be provided before currentGrid")

        symbols_in_use = values.get('symbolsInUse', [])
        if not symbols_in_use:
            raise ValueError("symbolsInUse must be provided and non-empty before currentGrid")

        if len(v) != grid_size:
            raise ValueError(f"Grid must have {grid_size} rows, got {len(v)}")

        for i, row in enumerate(v):
            if not isinstance(row, list):
                raise ValueError(f"Row {i} must be a list, got {type(row)}")
            if len(row) != grid_size:
                raise ValueError(f"Row {i} must have {grid_size} columns, got {len(row)}")

            for j, cell in enumerate(row):
                if not isinstance(cell, str):
                    raise ValueError(f"Cell at ({i},{j}) must be a string, got {type(cell)}: {cell}")
                if cell != "?" and cell not in symbols_in_use:
                    raise ValueError(
                        f"Invalid cell value at ({i},{j}): '{cell}'. Must be '?' or one of {symbols_in_use}")

        return v


class LLMPlayerTurnResponse(BaseModel):
    """LLM玩家单次行动响应"""
    action: Literal["observe", "guess", "final_guess", "give_up"]
    cellsToObserve: Optional[List[PositionModel]] = None
    guessGrid: Optional[Grid] = None
    reasoning: str = ""
    confidence: Optional[float] = None
    # 添加token使用量信息
    input_tokens: Optional[int] = 0
    output_tokens: Optional[int] = 0


# API请求/响应模型
class DesignPatternRequest(BaseModel):
    gridSize: int = Field(..., ge=3, le=6)
    numSymbols: int = Field(..., ge=2, le=len(ALL_SYMBOLS_PY))
    llmModel: Optional[str] = "openai_official/chatgpt-4o-latest"
    llmModelParams: Optional[LLMModelParams] = None
    prompt: Optional[str] = None


class DesignPatternResponse(BaseModel):
    pattern: Grid


# 游戏保存相关模型
class PlayerStateInGame(BaseModel):
    player_name_in_game: str
    player_type: Literal["Human", "LLM"]
    player_llm_model: Optional[str] = None
    player_llm_model_params: Optional[LLMModelParams] = None
    final_score: int
    final_guess: Optional[List[List[str]]] = None
    action_log: Optional[List[str]] = None
    queried_cells: Optional[List[PositionModel]] = None
    input_tokens: Optional[int] = 0
    output_tokens: Optional[int] = 0


class GameCreateRequest(BaseModel):
    grid_size: int
    num_symbols: int
    designer_type: Literal["Human", "LLM"]
    designer_llm_model: Optional[str] = None
    designer_llm_model_params: Optional[LLMModelParams] = None
    designer_pattern_mode: Optional[str] = None
    master_pattern: List[List[str]]
    game_config_dump: Dict[str, Any]
    players: List[PlayerStateInGame]
    test_set_id: Optional[str] = None


class GameCreateResponse(BaseModel):
    message: str
    game_id: str


# 游戏历史相关模型
class GameSummaryItem(BaseModel):
    id: str
    created_at: datetime
    grid_size: int
    num_symbols: int
    designer_type: str


class GamePlayerDetailResponse(BaseModel):
    player_name_in_game: str
    player_type: Literal["Human", "LLM"]
    player_llm_model: Optional[str] = None
    player_llm_model_params: Optional[LLMModelParams] = None
    final_score: Optional[int] = None
    final_guess: Optional[List[List[str]]] = None
    action_log: Optional[List[str]] = None
    queried_cells: Optional[List[PositionModel]] = None


class GameDetailResponse(BaseModel):
    id: str
    created_at: datetime
    grid_size: int
    num_symbols: int
    designer_type: Literal["Human", "LLM"]
    designer_llm_model: Optional[str] = None
    designer_llm_model_params: Optional[LLMModelParams] = None
    designer_pattern_mode: Optional[str] = None
    master_pattern: List[List[str]]
    game_config_dump: Dict[str, Any]
    players: List[GamePlayerDetailResponse]
    in_game_designer_score: Optional[float] = None
    meta_designer_score: Optional[float] = None


# 新增：测试集相关模型
class TestSetParticipant(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_name: str
    model_params: Optional[LLMModelParams] = None


class TestSetGameConfig(BaseModel):
    grid_size: int = Field(default=6, ge=3, le=6)
    num_symbols: int = Field(default=5, ge=2, le=6)
    optional_prompt: Optional[str] = None
    custom_pattern: Optional[List[List[str]]] = None
    repeat_count: int = Field(default=1, ge=1, le=10)


class TestSetCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    participants: List[TestSetParticipant]
    llm_rotate_designer: bool = True
    games: List[TestSetGameConfig]


class TestSetCreateResponse(BaseModel):
    test_set_id: str
    message: str


class TestSetListResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: str
    total_games: int
    completed_games: int
    created_at: datetime
    config: Optional[Dict[str, Any]] = None


class TestSetStatusUpdateRequest(BaseModel):
    status: str
    completed_games: Optional[int] = None


class LeaderboardEntry(BaseModel):
    model_name: str
    model_params: Optional[Dict[str, Any]]
    elo_rating: float
    trueskill_rating: float
    trueskill_mu: float
    trueskill_sigma: float
    games_as_player: int
    games_as_designer: int
    avg_score_as_player: float
    avg_score_as_designer: float
    avg_in_game_designer_score: float = 0.0
    avg_meta_designer_score: float = 0.0
    win_rate_as_player: float
    win_rate_as_designer: float
    wins_as_player: int = 0
    wins_as_designer: int = 0
    inter_designer_win_rate: float = 0.0  # Added inter-designer win rate field
    inter_designer_wins: int = 0  # Added inter-designer wins field
    total_games: int
    overall_win_rate: float = 0.0
    overall_wins: int = 0
    cost_per_game: float = 0.0
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class LeaderboardResponse(BaseModel):
    test_set_id: Optional[str]
    test_set_name: Optional[str]
    entries: List[LeaderboardEntry]
    generated_at: datetime


# --- 数据库连接 ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/patterns_db")
db_pool = None

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://*.vercel.app", "https://patterns2.vercel.app",
                   "https://www.haozhang.site"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# --- Chat History 管理函数 ---

def get_chat_history_key(game_id: str, player_id: str) -> str:
    """生成chat history的唯一键"""
    return f"{game_id}:{player_id}"


def get_player_messages(game_id: str, player_id: str) -> List[Dict[str, str]]:
    """获取特定玩家的消息历史"""
    key = get_chat_history_key(game_id, player_id)
    with chat_histories_lock:
        return chat_histories[key].copy()


def append_message(game_id: str, player_id: str, role: str, content: str):
    """向玩家的消息历史中添加消息"""
    key = get_chat_history_key(game_id, player_id)
    with chat_histories_lock:
        chat_histories[key].append({"role": role, "content": content})
        # 限制消息历史长度，避免token মারাত্মক
        if len(chat_histories[key]) > 50:  # 保留最近50条消息
            # 保留系统消息和最近的对话
            system_messages = [msg for msg in chat_histories[key] if msg["role"] == "system"]
            recent_messages = [msg for msg in chat_histories[key] if msg["role"] != "system"][-40:]
            chat_histories[key] = system_messages + recent_messages


def initialize_player_chat(game_id: str, player_id: str, player_name: str, grid_size: int,
                           symbols_in_use: List[Symbol]):
    """初始化玩家的对话历史"""
    key = get_chat_history_key(game_id, player_id)
    with chat_histories_lock:
        if key not in chat_histories:
            symbols_str = ", ".join(symbols_in_use)
            system_message = f"""You are {player_name}, a scientist playing Patterns II, a logic puzzle game.

GAME RULES:
- Grid size: {grid_size}×{grid_size}
- Available symbols: {symbols_str}
- Goal: Deduce the hidden pattern through strategic observation and logical reasoning
- Scoring: +1 for each correct unobserved cell, -1 for each incorrect unobserved cell, 0 for observed cells

COORDINATE SYSTEM:
- Rows are numbered 0 to {grid_size - 1} (top to bottom)
- Columns are numbered 0 to {grid_size - 1} (left to right)
- When referring to cells, always use (row, col) format with 0-based indexing
- Example: cell at row 0, column 1 is referenced as (0, 1)

YOUR AVAILABLE ACTIONS:
1. OBSERVE: Request to see specific cells (up to 3 cells per turn)
2. GUESS: Submit a complete {grid_size}×{grid_size} grid as your hypothesis (ends the game)
3. GIVE_UP: Forfeit the game (score = 0, avoid negative scores)

RESPONSE FORMAT:
Always respond with a JSON object:
{{
  "action": "observe" | "guess" | "give_up",
  "cellsToObserve": [optional, for observe: [{{"row": 0, "col": 1}}, ...]],
  "guessGrid": [optional, for guess: complete {grid_size}×{grid_size} grid],
  "reasoning": "Your very brief thought process",
  "confidence": 0.85 [optional, 0-1 scale for guess actions]
}}

IMPORTANT: Always use 0-based row and column indices in your cellsToObserve coordinates.
Row 0 is the top row, Column 0 is the leftmost column.

STRATEGY: Look for patterns like symmetry, repetition, gradients. Observe strategically before guessing.
Remember previous observations and build upon them to form hypotheses."""

            chat_histories[key] = [{"role": "system", "content": system_message}]


# --- Token使用跟踪函数 ---

def get_player_token_key(game_id: str, player_id: str) -> str:
    """生成玩家token跟踪的唯一键"""
    return f"{game_id}:{player_id}"


def add_player_token_usage(game_id: str, player_id: str, input_tokens: int, output_tokens: int):
    """添加玩家的token使用量"""
    key = get_player_token_key(game_id, player_id)
    with player_token_usage_lock:
        player_token_usage[key]["input_tokens"] += input_tokens
        player_token_usage[key]["output_tokens"] += output_tokens
        logger.info(
            f"玩家 {player_id} 累计token使用: 输入 {player_token_usage[key]['input_tokens']}, 输出 {player_token_usage[key]['output_tokens']}")


def get_player_token_usage(game_id: str, player_id: str) -> Tuple[int, int]:
    """获取玩家的累计token使用量"""
    key = get_player_token_key(game_id, player_id)
    with player_token_usage_lock:
        return player_token_usage[key]["input_tokens"], player_token_usage[key]["output_tokens"]


def clear_player_token_usage(game_id: str, player_id: str):
    """清理玩家的token使用记录"""
    key = get_player_token_key(game_id, player_id)
    with player_token_usage_lock:
        if key in player_token_usage:
            del player_token_usage[key]


# --- LLM Player 核心功能 ---

def is_openai_model(model_name: str) -> bool:
    """判断是否为OpenAI官方模型 - 基于缓存的模型列表或模型名称前缀"""
    # 检查是否以openai_official/开头 - 这是OpenAI官方模型
    if model_name.startswith("openai_official/"):
        return True

    # 检查缓存的OpenAI官方模型列表（不带前缀的原始模型名）
    clean_model_name = model_name.replace("openai_official/", "")
    with openai_models_cache_lock:
        return clean_model_name in openai_models_cache


def build_openrouter_params(model_params: Optional[LLMModelParams]) -> Dict[str, Any]:
    """构建OpenRouter API调用参数"""
    params = {}
    if model_params:
        if model_params.temperature is not None:
            params["temperature"] = model_params.temperature
        if model_params.maxCompletionTokens is not None:
            params["max_completion_tokens"] = model_params.maxCompletionTokens
        if model_params.topP is not None:
            params["top_p"] = model_params.topP
        if model_params.frequencyPenalty is not None:
            params["frequency_penalty"] = model_params.frequencyPenalty
        if model_params.presencePenalty is not None:
            params["presence_penalty"] = model_params.presence_penalty

    # 设置默认值
    if "temperature" not in params:
        params["temperature"] = 0.3
    if "max_completion_tokens" not in params:
        params["max_completion_tokens"] = 4096

    return params


def build_openai_params(model_params: Optional[LLMModelParams], model_name: str = "") -> Dict[str, Any]:
    """构建OpenAI API调用参数 - 统一使用max_completion_tokens"""
    params = {}

    if model_params:
        if model_params.temperature is not None:
            params["temperature"] = model_params.temperature
        if model_params.maxCompletionTokens is not None:
            params["max_completion_tokens"] = model_params.maxCompletionTokens
        if model_params.topP is not None:
            params["top_p"] = model_params.topP
        if model_params.frequencyPenalty is not None:
            params["frequency_penalty"] = model_params.frequencyPenalty
        if model_params.presencePenalty is not None:
            params["presence_penalty"] = model_params.presence_penalty

    # 设置默认值
    if "temperature" not in params:
        params["temperature"] = 0.3
    if "max_completion_tokens" not in params:
        params["max_completion_tokens"] = 4096

    return params


def build_llm_player_prompt(
        grid_size: int,
        symbols_in_use: List[Symbol],
        current_grid: List[List[str]],
        turn_number: int,
        player_name: str
) -> str:
    """构建LLM玩家的当前回合提示词 - 使用统一的坐标系统"""

    symbols_str = ", ".join(symbols_in_use)
    grid_display = format_grid_for_display(current_grid, grid_size)
    total_cells = grid_size * grid_size
    observed_cells = sum(1 for row in current_grid for cell in row if cell != "?" and cell is not None)
    unknown_cells = total_cells - observed_cells

    prompt = f"""CURRENT GAME STATE (Turn {turn_number}):
Observed cells: {observed_cells}/{total_cells}
Unknown cells: {unknown_cells}

Current Grid (? = unknown):
{grid_display}

COORDINATE SYSTEM REMINDER:
- Use 0-based indexing: rows 0-{grid_size - 1}, columns 0-{grid_size - 1}
- Row 0 is the TOP row, Column 0 is the LEFT column
- When observing cells, use {{"row": X, "col": Y}} format

Analyze the current grid and decide your next action. Consider:
- What patterns can you detect from the observed cells?
- Which cells would give you the most information if observed?
- What have you learned from previous observations?
- Can you make a reasonable guess based on the current grid?

Don't forget your goal is to maximize your score by making the best guess with the least number of observations to get the highest score.

Remember: Only use symbols from: {symbols_str}
Use 0-based coordinates: row 0-{grid_size - 1}, col 0-{grid_size - 1}

What is your next move?"""

    return prompt


def build_error_correction_prompt(
        grid_size: int,
        symbols_in_use: List[Symbol],
        current_grid: List[List[str]],
        turn_number: int,
        player_name: str,
        error_message: str,
        previous_response: str
) -> str:
    """构建错误修正提示词"""

    symbols_str = ", ".join(symbols_in_use)
    grid_display = format_grid_for_display(current_grid, grid_size)
    total_cells = grid_size * grid_size
    observed_cells = sum(1 for row in current_grid for cell in row if cell != "?" and cell is not None)
    unknown_cells = total_cells - observed_cells

    prompt = f"""CURRENT GAME STATE (Turn {turn_number}):
Observed cells: {observed_cells}/{total_cells}
Unknown cells: {unknown_cells}

Current Grid (? = unknown):
{grid_display}

ERROR CORRECTION NEEDED:
Your previous response had an error: {error_message}

Your previous response was:
{previous_response}

Please correct the error and provide a valid response. Make sure to:
1. Follow the exact JSON format required
2. Use only the provided symbols: {symbols_str}
3. Ensure all coordinates are within bounds (0 to {grid_size - 1})
4. Use 0-based indexing: row 0-{grid_size - 1}, col 0-{grid_size - 1}
5. If guessing, provide a complete {grid_size}×{grid_size} grid
6. Double-check your response format before submitting

COORDINATE SYSTEM REMINDER:
- Row 0 is the TOP row, Column 0 is the LEFT column
- Use {{"row": X, "col": Y}} format for coordinates

Remember: Only use symbols from: {symbols_str}
Use 0-based coordinates: row 0-{grid_size - 1}, col 0-{grid_size - 1}

Please provide a corrected response:"""

    return prompt


def build_design_error_correction_prompt(
        grid_size: int,
        num_symbols: int,
        available_symbols: List[str],
        user_prompt: Optional[str],
        error_message: str,
        previous_response: str
) -> str:
    """构建设计模式错误修正提示词"""
    symbols_str = ", ".join(available_symbols)

    base_requirement = "Design a pattern"
    if user_prompt and user_prompt.strip():
        requirement = f"{base_requirement} that follows the design requirement: {user_prompt.strip()}"
    else:
        requirement = base_requirement

    prompt = f"""ERROR CORRECTION NEEDED for Pattern Design:

ORIGINAL REQUIREMENT: {requirement}
GRID SIZE: {grid_size}x{grid_size}
AVAILABLE SYMBOLS: {symbols_str} (total: {num_symbols})

Your previous response had an error: {error_message}

Your previous response was:
{previous_response}

Please correct the error and provide a valid response. Make sure to:
1. Return a valid JSON object with the exact structure required
2. Use only the provided symbols: {symbols_str}
3. Ensure the pattern is a {grid_size}x{grid_size} 2D array
4. Each cell must contain one of the valid symbols
5. The pattern should be logically deducible, not random
6. Do NOT include any extra text, explanation, or formatting outside the JSON

REQUIRED JSON FORMAT:
{{
  "pattern": [
    ["symbol", "symbol", ...],
    ["symbol", "symbol", ...],
    ...
  ],
  "description": "Brief explanation of the pattern logic"
}}

Please provide a corrected response:"""

    return prompt


async def llm_player_turn_logic_with_retry(request: LLMPlayerTurnRequest,
                                           max_retries: int = 10) -> Tuple[LLMPlayerTurnResponse, int, int]:
    """带重试机制的LLM玩家回合逻辑，返回响应和token使用量"""
    last_error = None
    last_response = None
    total_input_tokens = 0
    total_output_tokens = 0

    for retry_count in range(max_retries + 1):
        try:
            if retry_count == 0:
                # 第一次尝试，使用正常逻辑
                response, input_tokens, output_tokens = await llm_player_turn_logic(request)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

                # 记录token使用量
                add_player_token_usage(request.gameId, request.playerId, input_tokens, output_tokens)

                return response, total_input_tokens, total_output_tokens
            else:
                # 重试时，使用错误修正逻辑
                logger.info(f"LLM玩家 {request.playerName} 第 {retry_count} 次重试")

                # 构建错误修正提示词
                error_prompt = build_error_correction_prompt(
                    request.gridSize,
                    request.symbolsInUse,
                    request.currentGrid,
                    request.turnNumber,
                    request.playerName,
                    str(last_error),
                    last_response or "No previous response"
                )

                # 添加错误修正消息到对话历史
                append_message(request.gameId, request.playerId, "user", error_prompt)

                # 获取完整的消息历史用于API调用
                messages = get_player_messages(request.gameId, request.playerId)

                # 判断使用哪个API
                if is_openai_model(request.llmModel):
                    if not openai_client:
                        raise Exception("OpenAI API客户端未初始化，请检查OPENAI_API_KEY环境变量")

                    # 构建OpenAI API参数
                    api_params = build_openai_params(request.llmModelParams, request.llmModel)

                    # 移除 openai_official/ 前缀获取真实模型名
                    clean_model_name = request.llmModel.replace("openai_official/", "")

                    # 调用OpenAI官方API
                    response = await openai_client.chat.completions.create(
                        model=clean_model_name,
                        messages=messages,
                        response_format={"type": "json_object"},
                        **api_params
                    )

                    # 提取token使用量
                    input_tokens = 0
                    output_tokens = 0
                    if hasattr(response, 'usage') and response.usage:
                        input_tokens = response.usage.prompt_tokens or 0
                        output_tokens = response.usage.completion_tokens or 0

                else:
                    if not openrouter_client:
                        raise Exception("OpenRouter API客户端未初始化，请检查OPENROUTER_API_KEY环境变量")

                    # 构建OpenRouter API参数
                    api_params = build_openrouter_params(request.llmModelParams)

                    # 调用OpenRouter API
                    response_data = await openrouter_client.post(
                        "/chat/completions",
                        json={
                            "model": request.llmModel or "openai/gpt-4o-mini",
                            "messages": messages,
                            "response_format": {"type": "json_object"},
                            **api_params
                        }
                    )
                    response_json = response_data.json()

                    # 提取token使用量
                    input_tokens = 0
                    output_tokens = 0
                    if 'usage' in response_json:
                        usage = response_json['usage']
                        input_tokens = usage.get('prompt_tokens', 0)
                        output_tokens = usage.get('completion_tokens', 0)

                    # 转换为类似OpenAI的响应格式
                    class MockResponse:
                        def __init__(self, json_data):
                            self.choices = [MockChoice(json_data['choices'][0])]

                    class MockChoice:
                        def __init__(self, choice_data):
                            self.message = MockMessage(choice_data['message'])

                    class MockMessage:
                        def __init__(self, message_data):
                            self.content = message_data['content']

                    response = MockResponse(response_json)

                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

                # 记录token使用量
                add_player_token_usage(request.gameId, request.playerId, input_tokens, output_tokens)

                # 解析响应
                content = response.choices[0].message.content
                if not content:
                    raise Exception("LLM返回空响应")

                last_response = content

                # 将LLM响应添加到对话历史
                append_message(request.gameId, request.playerId, "assistant", content)

                # 解析LLM响应
                parsed_response = parse_llm_response(
                    content,
                    request.gridSize,
                    request.symbolsInUse
                )

                logger.info(f"LLM玩家 {request.playerName} 第 {retry_count} 次重试成功")
                return parsed_response, total_input_tokens, total_output_tokens

        except Exception as e:
            last_error = e
            last_response = getattr(e, 'response_content', last_response)
            logger.warning(f"LLM玩家 {request.playerName} 第 {retry_count + 1} 次尝试失败: {e}")

            # 如果是最后一次重试，抛出异常
            if retry_count == max_retries:
                logger.error(f"LLM玩家 {request.playerName} 达到最大重试次数 ({max_retries})，放弃")
                raise Exception(f"Maximum retries ({max_retries}) reached. Last error: {str(last_error)}")

            # 短暂延迟后重试
            await asyncio.sleep(1)

    # 这行代码理论上不会执行到，但为了类型安全
    raise Exception(f"Unexpected error in retry logic")


async def design_pattern_logic_with_retry(request: DesignPatternRequest, max_retries: int = 5) -> DesignPatternResponse:
    """带重试机制的设计模式逻辑"""
    last_error = None
    last_response = None

    for retry_count in range(max_retries + 1):
        try:
            if retry_count == 0:
                # 第一次尝试，使用正常逻辑
                return await design_pattern_logic(request)
            else:
                # 重试时，使用错误修正逻辑
                logger.info(f"设计模式生成第 {retry_count} 次重试")

                current_symbols = ALL_SYMBOLS_PY[:request.numSymbols]

                # 构建错误修正提示词
                error_prompt = build_design_error_correction_prompt(
                    request.gridSize,
                    request.numSymbols,
                    current_symbols,
                    request.prompt,
                    str(last_error),
                    last_response or "No previous response"
                )

                # 判断使用哪个API
                if is_openai_model(request.llmModel):
                    if not openai_client:
                        raise Exception("OpenAI API客户端未初始化，请检查OPENAI_API_KEY环境变量")

                    # 构建OpenAI API参数
                    api_params = build_openai_params(request.llmModelParams, request.llmModel)
                    # 对于设计模式，使用稍高的temperature以增加创造性
                    if "temperature" not in api_params or api_params["temperature"] < 0.5:
                        api_params["temperature"] = 0.7

                    # 移除 openai_official/ 前缀获取真实模型名
                    clean_model_name = request.llmModel.replace("openai_official/", "")

                    response = await openai_client.chat.completions.create(
                        model=clean_model_name,
                        messages=[
                            {"role": "user", "content": error_prompt}
                        ],
                        response_format={"type": "json_object"},
                        **api_params
                    )
                else:
                    if not openrouter_client:
                        raise Exception("OpenRouter API客户端未初始化，请检查OPENROUTER_API_KEY环境变量")

                    # 构建OpenRouter API参数
                    api_params = build_openrouter_params(request.llmModelParams)
                    # 对于设计模式，使用稍高的temperature以增加创造性
                    if "temperature" not in api_params or api_params["temperature"] < 0.5:
                        api_params["temperature"] = 0.7

                    response_data = await openrouter_client.post(
                        "/chat/completions",
                        json={
                            "model": request.llmModel or "openai/gpt-4o-mini",
                            "messages": [
                                {"role": "user", "content": error_prompt}
                            ],
                            "response_format": {"type": "json_object"},
                            **api_params
                        }
                    )
                    response_json = response_data.json()

                    # 转换为类似OpenAI的响应格式
                    class MockResponse:
                        def __init__(self, json_data):
                            self.choices = [MockChoice(json_data['choices'][0])]

                    class MockChoice:
                        def __init__(self, choice_data):
                            self.message = MockMessage(choice_data['message'])

                    class MockMessage:
                        def __init__(self, message_data):
                            self.content = message_data['content']

                    response = MockResponse(response_json)

                content = response.choices[0].message.content
                if not content:
                    raise Exception("LLM返回空响应")

                last_response = content

                parsed_response = json.loads(content)
                llm_pattern = parsed_response.get("pattern")

                if not llm_pattern:
                    raise ValueError("LLM响应中缺少pattern字段")

                # 使用改进的验证函数，支持字符串和数组格式
                validated_pattern = validate_pattern(llm_pattern, request.gridSize, current_symbols)

                logger.info(f"设计模式生成第 {retry_count} 次重试成功")
                return DesignPatternResponse(pattern=validated_pattern)

        except Exception as e:
            last_error = e
            last_response = getattr(e, 'response_content', last_response)
            logger.warning(f"设计模式生成第 {retry_count + 1} 次尝试失败: {e}")

            # 如果是最后一次重试，抛出异常
            if retry_count == max_retries:
                logger.error(f"设计模式生成达到最大重试次数 ({max_retries})，放弃")
                raise Exception(f"Maximum retries ({max_retries}) reached. Last error: {str(last_error)}")

            # 短暂延迟后重试
            await asyncio.sleep(1)

    # 这行代码理论上不会执行到，但为了类型安全
    raise Exception(f"Unexpected error in design pattern retry logic")


async def llm_player_turn_logic(request: LLMPlayerTurnRequest) -> Tuple[LLMPlayerTurnResponse, int, int]:
    """LLM玩家回合逻辑 - 从端点中提取出来供后台任务使用，返回响应和token使用量"""
    # 初始化玩家对话历史（如果是第一次）
    initialize_player_chat(
        request.gameId,
        request.playerId,
        request.playerName,
        request.gridSize,
        request.symbolsInUse
    )

    # 构建当前回合的提示词
    current_prompt = build_llm_player_prompt(
        request.gridSize,
        request.symbolsInUse,
        request.currentGrid,
        request.turnNumber,
        request.playerName
    )

    # 添加用户消息到对话历史
    append_message(request.gameId, request.playerId, "user", current_prompt)

    # 获取完整的消息历史用于API调用
    messages = get_player_messages(request.gameId, request.playerId)

    input_tokens = 0
    output_tokens = 0

    # 判断使用哪个API
    if is_openai_model(request.llmModel):
        if not openai_client:
            raise Exception("OpenAI API客户端未初始化，请检查OPENAI_API_KEY环境变量")

        # 构建OpenAI API参数
        api_params = build_openai_params(request.llmModelParams, request.llmModel)

        # 移除 openai_official/ 前缀获取真实模型名
        clean_model_name = request.llmModel.replace("openai_official/", "")

        # 调用OpenAI官方API
        response = await openai_client.chat.completions.create(
            model=clean_model_name,
            messages=messages,
            response_format={"type": "json_object"},
            **api_params
        )

        # 提取token使用量
        if hasattr(response, 'usage') and response.usage:
            input_tokens = response.usage.prompt_tokens or 0
            output_tokens = response.usage.completion_tokens or 0
            logger.info(f"OpenAI API token使用: 输入 {input_tokens}, 输出 {output_tokens}")

    else:
        if not openrouter_client:
            raise Exception("OpenRouter API客户端未初始化，请检查OPENROUTER_API_KEY环境变量")

        # 构建OpenRouter API参数
        api_params = build_openrouter_params(request.llmModelParams)

        # 调用OpenRouter API
        response_data = await openrouter_client.post(
            "/chat/completions",
            json={
                "model": request.llmModel or "openai/gpt-4o-mini",
                "messages": messages,
                "response_format": {"type": "json_object"},
                **api_params
            }
        )
        response_json = response_data.json()

        # 提取token使用量
        if 'usage' in response_json:
            usage = response_json['usage']
            input_tokens = usage.get('prompt_tokens', 0)
            output_tokens = usage.get('completion_tokens', 0)
            logger.info(f"OpenRouter API token使用: 输入 {input_tokens}, 输出 {output_tokens}")
        else:
            logger.warning(f"OpenRouter API 响应中缺少 'usage' 字段，模型: {request.llmModel}")

        # 转换为类似OpenAI的响应格式
        class MockResponse:
            def __init__(self, json_data):
                self.choices = [MockChoice(json_data['choices'][0])]

        class MockChoice:
            def __init__(self, choice_data):
                self.message = MockMessage(choice_data['message'])

        class MockMessage:
            def __init__(self, message_data):
                self.content = message_data['content']

        response = MockResponse(response_json)

    # 解析响应
    content = response.choices[0].message.content
    if not content:
        raise Exception("LLM返回空响应")

    # 将LLM响应添加到对话历史
    append_message(request.gameId, request.playerId, "assistant", content)

    # 解析LLM响应
    parsed_response = parse_llm_response(
        content,
        request.gridSize,
        request.symbolsInUse
    )

    return parsed_response, input_tokens, output_tokens


def format_grid_for_display(grid: List[List[str]], grid_size: int) -> str:
    """将网格格式化为易读的显示格式 - 使用统一的坐标系统"""
    if not grid or len(grid) == 0:
        return "Empty grid"

    # 创建列标题 (0, 1, 2, ...)
    header = "    " + "   ".join(str(i) for i in range(grid_size))
    lines = [header]

    # 添加分隔线
    separator = "  " + "---" * grid_size + "-" * (grid_size - 1)
    lines.append(separator)

    # 添加每一行 (行号在左侧)
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
    """解析LLM的JSON响应"""
    try:
        response_data = json.loads(response_text)
    except json.JSONDecodeError as e:
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

        # 验证猜测网格
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
    """验证LLM生成的模式是否符合要求"""
    if not isinstance(pattern, list) or len(pattern) != expected_size:
        raise ValueError(f"Pattern must be a {expected_size}x{expected_size} list")

    validated_grid: Grid = []
    for row_idx, row in enumerate(pattern):
        validated_row: List[Cell] = []

        # 处理字符串格式
        if isinstance(row, str):
            if len(row) != expected_size:
                raise ValueError(f"Row {row_idx} must have {expected_size} characters, got {len(row)}")

            for col_idx, char in enumerate(row):
                if char not in valid_symbols:
                    raise ValueError(f"Invalid symbol '{char}' at position ({row_idx},{col_idx})")
                validated_row.append(char)

        # 处理数组格式
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
    """构建LLM设计模式的系统提示词"""
    symbols_str = ", ".join(available_symbols)

    return f"""You are a creative and experienced logic puzzle designer. Your task is to design a unique pattern for the "Patterns II" deduction game.

GAME OVERVIEW:
- Grid size: {grid_size}x{grid_size}
- Available symbols: {symbols_str} (total: {num_symbols})
- Players will see only a few revealed cells and must deduce the entire pattern based on logical reasoning.

DESIGN GOALS:
1. The pattern should be logically deducible (not random noise).
2. Avoid overly simple patterns (e.g., all the same symbols).
3. Favor design principles like symmetry, gradients, rotation, or recursion.
4. Ensure that the full pattern can be reasonably deduced from partial observations.
5. Feel free to use your creativity, as long as the logic is consistent.

RESPONSE FORMAT (IMPORTANT):
You must return a valid JSON object with the this **exact structure**:
{{
  "pattern": [
    ["", "", ...],
    ["", "", ...],
    ...
  ],
  "description": "Very brief explanation of the underlying pattern logic and design inspiration."
}}

CRITICAL NOTES:
- Use only the provided symbols: {symbols_str}
- Ensure the pattern is a {grid_size}x{grid_size} 2D array of symbol strings.
- Do NOT include any extra text, explanation, markdown, or formatting outside the JSON."""


def build_user_prompt(user_prompt: Optional[str]) -> str:
    """构建用户自定义提示词"""
    base = "Design a pattern"
    if user_prompt and user_prompt.strip():
        return f"{base} that follows the design requirement: {user_prompt.strip()}"
    return base


# --- API端点逻辑函数 (供后台任务调用) ---

async def design_pattern_logic(request: DesignPatternRequest) -> DesignPatternResponse:
    """设计模式逻辑 - 从端点中提取出来供后台任务使用"""
    current_symbols = ALL_SYMBOLS_PY[:request.numSymbols]
    system_prompt = build_system_prompt(request.gridSize, request.numSymbols, current_symbols)
    user_prompt = build_user_prompt(request.prompt)

    # 判断使用哪个API
    if is_openai_model(request.llmModel):
        if not openai_client:
            raise Exception("OpenAI API客户端未初始化，请检查OPENAI_API_KEY环境变量")

        # 构建OpenAI API参数
        api_params = build_openai_params(request.llmModelParams, request.llmModel)
        # 对于设计模式，使用稍高的temperature以增加创造性
        if "temperature" not in api_params or api_params["temperature"] < 0.5:
            api_params["temperature"] = 0.7

        # 移除 openai_official/ 前缀获取真实模型名
        clean_model_name = request.llmModel.replace("openai_official/", "")

        response = await openai_client.chat.completions.create(
            model=clean_model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            **api_params
        )
    else:
        if not openrouter_client:
            raise Exception("OpenRouter API客户端未初始化，请检查OPENROUTER_API_KEY环境变量")

        # 构建OpenRouter API参数
        api_params = build_openrouter_params(request.llmModelParams)
        # 对于设计模式，使用稍高的temperature以增加创造性
        if "temperature" not in api_params or api_params["temperature"] < 0.5:
            api_params["temperature"] = 0.7

        response_data = await openrouter_client.post(
            "/chat/completions",
            json={
                "model": request.llmModel or "openai/gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                **api_params
            }
        )
        response_json = response_data.json()

        # 转换为类似OpenAI的响应格式
        class MockResponse:
            def __init__(self, json_data):
                self.choices = [MockChoice(json_data['choices'][0])]

        class MockChoice:
            def __init__(self, choice_data):
                self.message = MockMessage(choice_data['message'])

        class MockMessage:
            def __init__(self, message_data):
                self.content = message_data['content']

        response = MockResponse(response_json)

    content = response.choices[0].message.content
    if not content:
        raise Exception("LLM返回空响应")

    parsed_response = json.loads(content)
    llm_pattern = parsed_response.get("pattern")

    if not llm_pattern:
        raise ValueError("LLM响应中缺少pattern字段")

    # 使用改进的验证函数，支持字符串和数组格式
    validated_pattern = validate_pattern(llm_pattern, request.gridSize, current_symbols)

    return DesignPatternResponse(pattern=validated_pattern)


async def save_game_logic(request: GameCreateRequest) -> GameCreateResponse:
    """保存游戏逻辑的核心实现"""
    if not db_pool:
        raise Exception("Database not connected")

    async with db_pool.acquire() as conn:
        game_id = str(uuid.uuid4())

        # 序列化设计师模型参数
        designer_params_json = None
        if request.designer_llm_model_params:
            designer_params_json = json.dumps(request.designer_llm_model_params.dict(exclude_none=True))

        await conn.execute(
            """
            INSERT INTO games (id, grid_size, num_symbols, designer_type, designer_llm_model, designer_llm_model_params,
                               designer_pattern_mode, master_pattern, game_config_dump, test_set_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            game_id, request.grid_size, request.num_symbols, request.designer_type,
            request.designer_llm_model, designer_params_json, request.designer_pattern_mode,
            json.dumps(request.master_pattern), json.dumps(request.game_config_dump), request.test_set_id
        )

        # First, collect all player scores for player-only ranking
        player_scores = []
        for player in request.players:
            # Create a unique ID for player including model and params
            player_params_dict = player.player_llm_model_params.dict(
                exclude_none=True) if player.player_llm_model_params else {}
            player_id = f"{player.player_llm_model or 'unknown_model'}#{json.dumps(player_params_dict)}"
            player_scores.append((player_id, player.final_score or 0))

        # Calculate ranks for players only
        player_ranks = calculate_ranks(player_scores)

        # Calculate designer scores using the new algorithm
        player_score_values = [score for _, score in player_scores]
        in_game_designer_score, meta_designer_score = calculate_designer_scores(
            player_score_values,
            request.grid_size,
            request.num_symbols
        )

        logger.info(
            f"游戏 {game_id}: 设计师分数计算 - In-game: {in_game_designer_score:.2f}, Meta: {meta_designer_score:.2f}")

        # Collect all participant scores (players + designer) for overall ranking
        # Use in_game_designer_score for ranking instead of old designer_score
        all_participant_scores = player_scores.copy()
        designer_input_tokens = 0
        designer_output_tokens = 0
        designer_id = None

        if request.designer_type == "LLM" and request.designer_llm_model:
            designer_params_dict = request.designer_llm_model_params.dict(
                exclude_none=True) if request.designer_llm_model_params else {}
            designer_id = f"{request.designer_llm_model}#{json.dumps(designer_params_dict)}"
            all_participant_scores.append((designer_id, in_game_designer_score))

        all_ranks = calculate_ranks(all_participant_scores)

        for player in request.players:
            # Safety check for player_llm_model_params
            player_params_dict = player.player_llm_model_params.dict(
                exclude_none=True) if player.player_llm_model_params else {}
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

            logger.info(f"保存玩家 {player.player_name_in_game} token数据: 输入 {input_tokens}, 输出 {output_tokens}")
            await conn.execute(
                """
                INSERT INTO game_players (game_id, player_name_in_game, player_type, player_llm_model,
                                          player_llm_model_params,
                                          final_score, final_guess, action_log, queried_cells, input_tokens,
                                          output_tokens)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                game_id, player.player_name_in_game, player.player_type, player.player_llm_model, player_params_json,
                player.final_score,
                json.dumps(player.final_guess) if player.final_guess else None,
                json.dumps(player.action_log) if player.action_log else None,
                queried_cells_json,
                input_tokens, output_tokens
            )

            await conn.execute(
                """
                INSERT INTO game_analytics (game_id, grid_size, num_symbols, test_set_id,
                                            participant_role, participant_id, participant_name, participant_type,
                                            participant_llm_model, participant_llm_model_params,
                                            final_score, in_game_designer_score, meta_designer_score,
                                            rank_in_game, rank_in_game_incl_designer,
                                            observation_count, observation_rounds, did_quit,
                                            final_guess, action_log, queried_cells, master_pattern,
                                            input_tokens, output_tokens)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                        $22, $23, $24)
                """,
                game_id, request.grid_size, request.num_symbols, request.test_set_id,
                'player', player_id, player.player_name_in_game, player.player_type,
                player.player_llm_model, player_params_json,
                player.final_score, 0.0, 0.0,  # Players don't have designer scores
                player_rank, player_rank_incl_designer,
                analyze_action_log(player.action_log)[0], analyze_action_log(player.action_log)[1],
                analyze_action_log(player.action_log)[2],
                json.dumps(player.final_guess) if player.final_guess else None,
                json.dumps(player.action_log) if player.action_log else None,
                queried_cells_json,
                json.dumps(request.master_pattern),
                input_tokens, output_tokens
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
                                            input_tokens, output_tokens)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                        $22, $23, $24)
                """,
                game_id, request.grid_size, request.num_symbols, request.test_set_id,
                'designer', designer_id, 'Designer', request.designer_type,
                request.designer_llm_model, designer_params_json,
                int(in_game_designer_score), in_game_designer_score, meta_designer_score,
                0, designer_rank_incl_designer,
                0, 0, 0,
                None, None, None,
                json.dumps(request.master_pattern),
                designer_input_tokens, designer_output_tokens
            )

        logger.info(f"Game saved successfully with ID: {game_id}")
        return GameCreateResponse(message="Game saved successfully", game_id=game_id)


# --- 获取模型列表的逻辑函数 ---

async def get_available_models_logic() -> ModelsListResponse:
    """获取可用的模型列表 - 合并OpenRouter和OpenAI官方模型"""
    logger.info("正在获取模型列表...")

    all_models = []

    # 获取OpenRouter模型
    if openrouter_client:
        try:
            logger.info("正在从OpenRouter API获取模型列表...")
            response_data = await openrouter_client.get("/models")
            response_json = response_data.json()

            if 'data' in response_json:
                models_data = response_json['data']
                logger.info(f"从OpenRouter获取到 {len(models_data)} 个模型")

                # 过滤出适合的模型
                for model in models_data:
                    try:
                        model_id = model.get('id', '')
                        model_name = model.get('name', model_id)

                        # 跳过一些不适合的模型
                        if any(skip in model_id.lower() for skip in
                               ['embedding', 'whisper', 'tts', 'dall-e']):
                            continue

                        all_models.append(OpenRouterModel(
                            id=model_id,  # 保持原始ID，不添加前缀
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
                        logger.warning(f"跳过OpenRouter模型 {model.get('id', 'unknown')} 由于错误: {e}")
                        continue

        except Exception as e:
            logger.error(f"获取OpenRouter模型列表失败: {str(e)}")

    # 添加OpenAI官方模型 - 使用 openai_official/ 前缀
    with openai_models_cache_lock:
        cached_openai_models = openai_models_cache.copy()

    # 创建一个集合来跟踪已添加的模型ID，避免重复
    existing_model_ids = {model.id for model in all_models}

    for model_id in cached_openai_models:
        # 为OpenAI官方模型添加 openai_official/ 前缀
        prefixed_id = f"openai_official/{model_id}"

        # 检查是否已存在，避免重复
        if prefixed_id not in existing_model_ids:
            all_models.append(OpenRouterModel(
                id=prefixed_id,
                name=f"OpenAI Official: {model_id}",
                created=0,
                description=f"OpenAI官方 {model_id} 模型",
                context_length=None
            ))
            existing_model_ids.add(prefixed_id)

    logger.info(f"从缓存添加了 {len(cached_openai_models)} 个OpenAI官方模型")

    # 按模型名称排序，优先显示常用模型
    priority_models = [
        'openai_official/chatgpt-4o-latest',
    ]

    def sort_key(model):
        if model.id in priority_models:
            return (0, priority_models.index(model.id))
        return (1, model.id)

    all_models.sort(key=sort_key)

    logger.info(f"总共获取到 {len(all_models)} 个模型")

    # 如果没有获取到任何模型，返回默认模型列表
    if not all_models:
        logger.info("返回默认模型列表")
        default_models = [
            OpenRouterModel(id="openai_official/chatgpt-4o-latest", name="OpenAI Official: ChatGPT-4o Latest",
                            created=0),
            OpenRouterModel(id="openai_official/gpt-4o-mini", name="OpenAI Official: GPT-4o Mini", created=0),
            OpenRouterModel(id="openai_official/gpt-4o", name="OpenAI Official: GPT-4o", created=0),
            OpenRouterModel(id="openai/gpt-4o-mini", name="GPT-4o Mini (via OpenRouter)", created=0),
            OpenRouterModel(id="deepseek/deepseek-chat", name="DeepSeek Chat", created=0),
            OpenRouterModel(id="anthropic/claude-3-5-sonnet", name="Claude 3.5 Sonnet", created=0),
            OpenRouterModel(id="meta-llama/llama-3.1-8b-instruct", name="Llama 3.1 8B Instruct", created=0),
            OpenRouterModel(id="qwen/qwen-2.5-7b-instruct", name="Qwen 2.5 7B Instruct", created=0),
        ]
        return ModelsListResponse(
            object="list",
            data=default_models
        )

    # 返回合并后的模型列表
    return ModelsListResponse(
        object="list",
        data=all_models
    )


# --- API端点 ---

@app.get("/api/models", response_model=ModelsListResponse)
@app.post("/api/models", response_model=ModelsListResponse)
async def get_available_models():
    """获取可用的模型列表 - 支持GET和POST请求"""
    logger.info("收到获取模型列表的请求")
    return await get_available_models_logic()


@app.post("/api/llm-player-turn", response_model=LLMPlayerTurnResponse)
async def llm_player_turn_endpoint(request: LLMPlayerTurnRequest):
    """LLM玩家单次行动API"""
    logger.info(f"LLM玩家 {request.playerName} 第{request.turnNumber}回合开始")

    try:
        response, input_tokens, output_tokens = await llm_player_turn_logic_with_retry(request)
        logger.info(f"LLM玩家 {request.playerName} 使用了 {input_tokens} 输入token, {output_tokens} 输出token")

        # 在响应中包含token信息
        response.input_tokens = input_tokens
        response.output_tokens = output_tokens

        return response

    except Exception as e:
        logger.error(f"LLM API错误: {e}")
        error_message = str(e)

        if "insufficient_quota" in error_message.lower() or "quota" in error_message.lower():
            error_message = "API配额不足"
        elif "rate_limit" in error_message.lower():
            error_message = "API调用频率过高，请稍后重试"
        elif "max_completion_tokens" in error_message.lower():
            error_message = f"参数错误: {error_message}. 请检查模型参数配置。"

        raise HTTPException(status_code=502, detail=f"LLM API错误: {error_message}")


@app.post("/api/design-pattern", response_model=DesignPatternResponse)
async def design_pattern_endpoint(request: DesignPatternRequest):
    """使用LLM生成游戏模式"""
    try:
        return await design_pattern_logic_with_retry(request)

    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}")
        raise HTTPException(status_code=500, detail=f"LLM返回的JSON格式无效: {str(e)}")

    except ValueError as e:
        logger.error(f"模式验证失败: {e}")
        raise HTTPException(status_code=500, detail=f"模式验证失败: {str(e)}")

    except Exception as e:
        logger.error(f"Pattern design failed: {e}")
        raise HTTPException(status_code=500, detail=f"Pattern design failed: {str(e)}")


@app.post("/api/games", response_model=GameCreateResponse, status_code=201)
async def save_game_endpoint(request: GameCreateRequest):
    """保存完成的游戏数据"""
    try:
        return await save_game_logic(request)
    except Exception as e:
        logger.error(f"Failed to save game: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save game: {str(e)}")


@app.get("/api/games")
async def get_games_list_endpoint(limit: int = 50, offset: int = 0, test_set_id: Optional[str] = None):
    """获取游戏历史列表"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            if test_set_id:
                # 获取特定测试集的游戏
                rows = await conn.fetch(
                    "SELECT id, created_at, grid_size, num_symbols, designer_type FROM games WHERE test_set_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                    test_set_id, limit, offset
                )
            else:
                # 获取所有游戏
                rows = await conn.fetch(
                    "SELECT id, created_at, grid_size, num_symbols, designer_type FROM games ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                    limit, offset
                )

            # 直接返回字典列表，确保UUID被正确转换
            games = []
            for row in rows:
                game_dict = {
                    'id': str(row['id']),
                    'created_at': row['created_at'].isoformat(),
                    'grid_size': row['grid_size'],
                    'num_symbols': row['num_symbols'],
                    'designer_type': row['designer_type']
                }
                games.append(game_dict)

            logger.info(f"Retrieved {len(games)} games from database")
            return games

    except Exception as e:
        logger.error(f"Failed to get games list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get games list: {str(e)}")


@app.get("/api/games/{game_id}")
async def get_game_details_endpoint(game_id: str):
    """获取特定游戏的详细信息，包括计算的设计师分数"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            game_row = await conn.fetchrow("SELECT * FROM games WHERE id = $1", game_id)
            if not game_row:
                raise HTTPException(status_code=404, detail="Game not found")

            player_rows = await conn.fetch("SELECT * FROM game_players WHERE game_id = $1 ORDER BY final_score DESC",
                                           game_id)

            players = []
            player_scores = []

            for row in player_rows:
                player_data = {
                    'player_name_in_game': row['player_name_in_game'],
                    'player_type': row['player_type'],
                    'player_llm_model': row['player_llm_model'],
                    'player_llm_model_params': None,
                    'final_score': row['final_score'] or 0,
                    'final_guess': None,
                    'action_log': None,
                    'queried_cells': []
                }

                # 收集玩家分数用于计算设计师分数
                if row['final_score'] is not None:
                    player_scores.append(row['final_score'])

                # 解析玩家模型参数
                if row['player_llm_model_params']:
                    try:
                        player_data['player_llm_model_params'] = json.loads(row['player_llm_model_params'])
                    except Exception as e:
                        logger.warning(f"Failed to parse player_llm_model_params: {e}")

                # 安全地解析JSON字段
                if row['final_guess']:
                    try:
                        player_data['final_guess'] = json.loads(row['final_guess'])
                    except Exception as e:
                        logger.warning(f"Failed to parse final_guess for player: {e}")

                if row['action_log']:
                    try:
                        player_data['action_log'] = json.loads(row['action_log'])
                    except Exception as e:
                        logger.warning(f"Failed to parse action_log for player: {e}")

                # 特别处理 queried_cells
                if row['queried_cells']:
                    try:
                        queried_data = json.loads(row['queried_cells'])
                        player_data['queried_cells'] = queried_data
                    except Exception as e:
                        logger.warning(f"Failed to parse queried_cells: {e}")
                        player_data['queried_cells'] = []

                players.append(player_data)

            # Calculate designer scores using the new algorithm
            in_game_designer_score = None
            meta_designer_score = None
            if game_row['designer_type'] == 'LLM' and len(player_scores) > 0:
                in_game_designer_score, meta_designer_score = calculate_designer_scores(
                    player_scores, game_row['grid_size'], game_row['num_symbols']
                )
                designer_score = int(round(in_game_designer_score))  # Cast to int for score consistency

            # 解析设计师模型参数
            designer_model_params = None
            if game_row['designer_llm_model_params']:
                try:
                    designer_model_params = json.loads(game_row['designer_llm_model_params'])
                except Exception as e:
                    logger.warning(f"Failed to parse designer_llm_model_params: {e}")

            # 构建游戏数据字典
            game_data = {
                'id': str(game_row['id']),
                'created_at': game_row['created_at'].isoformat(),
                'grid_size': game_row['grid_size'],
                'num_symbols': game_row['num_symbols'],
                'designer_type': game_row['designer_type'],
                'designer_llm_model': game_row['designer_llm_model'],
                'designer_llm_model_params': designer_model_params,
                'designer_pattern_mode': game_row['designer_pattern_mode'],
                'master_pattern': json.loads(game_row['master_pattern']),
                'game_config_dump': json.loads(game_row['game_config_dump']),
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


# --- 测试集和排行榜API ---

@app.post("/api/test-sets", response_model=TestSetCreateResponse)
async def create_test_set_endpoint(request: TestSetCreateRequest):
    """创建测试集"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        test_set_id = str(uuid.uuid4())

        # 计算总游戏数
        total_games = 0
        for game_config in request.games:
            if request.llm_rotate_designer:
                # 轮流设计师模式：每个参与者都当一次设计师
                total_games += len(request.participants) * game_config.repeat_count
            else:
                # 固定设计师模式：只有一局
                total_games += game_config.repeat_count

        config_data = {
            "participants": [
                {
                    "model_name": p.model_name,
                    "model_params": p.model_params.dict(exclude_none=True) if p.model_params else None
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
                    "repeat_count": g.repeat_count
                }
                for g in request.games
            ]
        }

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO test_sets (id, name, description, config, total_games)
                VALUES ($1, $2, $3, $4, $5)
                """,
                test_set_id, request.name, request.description,
                json.dumps(config_data), total_games
            )

        logger.info(f"Test set created successfully with ID: {test_set_id}")
        return TestSetCreateResponse(
            test_set_id=test_set_id,
            message="Test set created successfully"
        )

    except Exception as e:
        logger.error(f"Failed to create test set: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create test set: {str(e)}")


@app.get("/api/test-sets", response_model=List[TestSetListResponse])
async def get_test_sets_endpoint():
    """获取测试集列表"""
    logger.info("获取测试集列表请求")

    if not db_pool:
        logger.error("数据库连接池未初始化")
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, description, status, total_games, completed_games, created_at, config FROM test_sets ORDER BY created_at DESC"
            )

            test_sets = []
            for row in rows:
                # 解析配置
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
                    config=config
                )
                test_sets.append(test_set)

            logger.info(f"成功获取 {len(test_sets)} 个测试集")
            return test_sets

    except Exception as e:
        logger.error(f"Failed to get test sets: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get test sets: {str(e)}")


@app.get("/api/test-sets/{test_set_id}")
async def get_test_set_details_endpoint(test_set_id: str):
    """获取测试集详细信息"""
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
                'created_at': row['created_at'].isoformat()
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get test set details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get test set details: {str(e)}")


@app.delete("/api/test-sets/{test_set_id}")
async def delete_test_set_endpoint(test_set_id: str):
    """删除测试集"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            # 首先删除相关的游戏数据
            await conn.execute("DELETE FROM games WHERE test_set_id = $1", test_set_id)

            # 然后删除测试集
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
    """更新测试集状态"""
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
    """获取排行榜"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        calculator = LeaderboardCalculator(db_pool)

        # 首先尝试从缓存获取排行榜数据
        if not force_recalculate:
            cached_data = await calculator.get_cached_leaderboard(test_set_id)
            if cached_data:
                logger.info("使用缓存的排行榜数据")

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

        # 如果没有缓存或强制重新计算，则计算新的排行榜
        logger.info(f"计算新的排行榜数据，force_recalculate={force_recalculate}")
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
    """开始执行测试集"""
    if not db_pool or not background_tasks_manager:
        raise HTTPException(status_code=500, detail="Service not ready")

    try:
        # 启动后台任务
        await background_tasks_manager.start_test_set_execution(test_set_id)

        logger.info(f"Test set {test_set_id} started in background")
        return {"message": "Test set started successfully", "test_set_id": test_set_id}

    except Exception as e:
        logger.error(f"Failed to start test set: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start test set: {str(e)}")


@app.get("/api/test-sets/{test_set_id}/current-game")
async def get_current_game_state_endpoint(test_set_id: str):
    """获取当前游戏状态（用于实时观看）"""
    with game_states_lock:
        if test_set_id in current_game_states:
            return current_game_states[test_set_id]
        else:
            return {"message": "No active game for this test set"}


@app.get("/health")
async def health_check():
    """健康检查"""
    db_status = "connected" if db_pool else "disconnected"
    openrouter_status = "configured" if openrouter_client else "not configured"
    openai_status = "configured" if openai_client else "not configured"

    return {
        "status": "healthy",
        "openrouter_configured": bool(openrouter_client),
        "openai_configured": bool(openai_client),
        "database_connected": bool(db_pool),
        "database_status": db_status,
        "openrouter_status": openrouter_status,
        "openai_status": openai_status
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
