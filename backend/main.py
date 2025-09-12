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
from typing import List, Optional, Literal, Dict, Any, Union, Set
import asyncpg
from contextlib import asynccontextmanager
import together
from dotenv import load_dotenv
import threading
from collections import defaultdict
import openai

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


    async def create_leaderboard_tables(db_pool):
        pass

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 获取API密钥
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 全局OpenAI模型列表缓存
openai_models_cache: Set[str] = set()
openai_models_cache_lock = threading.Lock()

# 初始化Together.ai客户端
together_client = None
if TOGETHER_API_KEY:
    try:
        together_client = together.Together(api_key=TOGETHER_API_KEY)
        logger.info("Together.ai客户端初始化成功")
    except Exception as e:
        logger.error(f"Together.ai客户端初始化失败: {e}")
        together_client = None
else:
    logger.warning("TOGETHER_API_KEY环境变量未设置")

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

                # 直接调用design pattern端点的逻辑
                design_request = DesignPatternRequest(**request_data)
                design_response = await design_pattern_logic(design_request)
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
                'isPaused': False
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
                    # 使用现有的LLM player turn API
                    llm_request_data = {
                        'playerId': player['id'],
                        'playerName': player['name'],
                        'gameId': game_id,
                        'gridSize': game_config['baseSettings']['gridSize'],
                        'symbolsInUse': symbols_in_use,
                        'currentGrid': player_state['grid'],
                        'llmModel': player.get('llmModel', 'deepseek-ai/DeepSeek-V3'),
                        'llmModelParams': player.get('llmModelParams'),
                        'turnNumber': player_state['turnNumber']
                    }

                    llm_request = LLMPlayerTurnRequest(**llm_request_data)
                    llm_response = await llm_player_turn_logic(llm_request)

                    player_state['isWaitingForLLM'] = False
                    player_state['turnNumber'] += 1

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
                        f"Turn {player_state['turnNumber'] - 1}: Error occurred, game ended. {str(e)}")

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
                    queried_cells=queried_cells
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


async def initialize_openai_models_cache():
    """初始化OpenAI模型缓存"""
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
        logger.info("正在从OpenAI API获取模型列表以初始化缓存...")
        models_response = await openai_client.models.list()

        # 需要排除的关键词
        exclude_keywords = ["embedding", "whisper", "tts", "dall-e"]

        openai_model_ids = set()
        for model in models_response.data:
            model_id = model.id.lower()
            # 跳过包含排除关键词的模型
            if any(keyword in model_id for keyword in exclude_keywords):
                continue
            openai_model_ids.add(model.id)  # 保持原始大小写

        with openai_models_cache_lock:
            openai_models_cache.clear()
            openai_models_cache.update(openai_model_ids)

        logger.info(f"成功缓存了 {len(openai_model_ids)} 个OpenAI模型")

    except Exception as e:
        logger.error(f"获取OpenAI模型列表失败，使用默认列表: {e}")
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
    """自动创建数据库表（如果不存在）"""
    if not db_pool:
        return

    try:
        async with db_pool.acquire() as conn:
            # 检查表是否存在
            result = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'games')"
            )

            if not result:
                logger.info("数据库表不存在，正在创建...")

                # 创建 games 表
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

                # 创建 game_players 表
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
                                       queried_cells           JSONB
                                   )
                                   """)

                # 创建 test_sets 表 - 修改默认状态为 'created'
                await conn.execute("""
                                   CREATE TABLE IF NOT EXISTS test_sets
                                   (
                                       id
                                       TEXT
                                       PRIMARY
                                       KEY,
                                       name
                                       TEXT
                                       NOT
                                       NULL,
                                       description
                                       TEXT,
                                       config
                                       JSONB
                                       NOT
                                       NULL,
                                       status
                                       TEXT
                                       DEFAULT
                                       'created',
                                       total_games
                                       INTEGER
                                       DEFAULT
                                       0,
                                       completed_games
                                       INTEGER
                                       DEFAULT
                                       0,
                                       created_at
                                       TIMESTAMPTZ
                                       DEFAULT
                                       NOW
                                   (
                                   )
                                       )
                                   """)

                # 创建索引
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_players_game_id ON game_players(game_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_created_at ON games(created_at DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_test_set_id ON games(test_set_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_created_at ON test_sets(created_at DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_status ON test_sets(status)")

                logger.info("数据库表创建成功！")
            else:
                logger.info("数据库表已存在")

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


class TogetherModel(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "together"
    type: Optional[str] = None
    display_name: Optional[str] = None
    organization: Optional[str] = None
    context_length: Optional[int] = None
    link: Optional[str] = None
    license: Optional[str] = None
    pricing: Optional[Any] = None  # Changed from Dict[str, Any] to Any to handle PricingObject


class ModelsListResponse(BaseModel):
    object: str = "list"
    data: List[TogetherModel]


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
    llmModel: Optional[str] = "deepseek-ai/DeepSeek-V3"
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


# API请求/响应模型
class DesignPatternRequest(BaseModel):
    gridSize: int = Field(..., ge=3, le=6)
    numSymbols: int = Field(..., ge=2, le=len(ALL_SYMBOLS_PY))
    llmModel: Optional[str] = "deepseek-ai/DeepSeek-V3"
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
    win_rate_as_player: float
    win_rate_as_designer: float
    total_games: int
    overall_win_rate: float = 0.0  # 添加默认值
    overall_wins: int = 0  # 添加默认值


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
        # 限制消息历史长度，避免token过多
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
  "reasoning": "Your detailed thought process",
  "confidence": 0.85 [optional, 0-1 scale for guess actions]
}}

STRATEGY: Look for patterns like symmetry, repetition, gradients. Observe strategically before guessing.
Remember previous observations and build upon them to form hypotheses."""

            chat_histories[key] = [{"role": "system", "content": system_message}]


# --- LLM Player 核心功能 ---

def is_openai_model(model_name: str) -> bool:
    """判断是否为OpenAI模型 - 基于缓存的模型列表"""
    with openai_models_cache_lock:
        return model_name in openai_models_cache


def build_together_params(model_params: Optional[LLMModelParams]) -> Dict[str, Any]:
    """构建Together.ai API调用参数"""
    params = {}
    if model_params:
        if model_params.temperature is not None:
            params["temperature"] = model_params.temperature
        if model_params.maxCompletionTokens is not None:
            params["max_completion_tokens"] = model_params.maxCompletionTokens  # Together.ai uses max_completion_tokens
        if model_params.topP is not None:
            params["top_p"] = model_params.topP
        if model_params.frequencyPenalty is not None:
            params["frequency_penalty"] = model_params.frequencyPenalty
        if model_params.presencePenalty is not None:
            params["presence_penalty"] = model_params.presencePenalty

    # 设置默认值
    if "temperature" not in params:
        params["temperature"] = 0.3
    if "max_completion_tokens" not in params:
        params["max_completion_tokens"] = 2000

    return params


def build_openai_params(model_params: Optional[LLMModelParams]) -> Dict[str, Any]:
    """构建OpenAI API调用参数"""
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
            params["presence_penalty"] = model_params.presencePenalty

    # 设置默认值
    if "temperature" not in params:
        params["temperature"] = 0.3
    if "max_completion_tokens" not in params:
        params["max_completion_tokens"] = 2000

    return params


def build_llm_player_prompt(
        grid_size: int,
        symbols_in_use: List[Symbol],
        current_grid: List[List[str]],
        turn_number: int,
        player_name: str
) -> str:
    """构建LLM玩家的当前回合提示词"""

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

Analyze the current grid and decide your next action. Consider:
- What patterns can you detect from the observed cells?
- Which cells would give you the most information if observed?
- What have you learned from previous observations?
- Can you make a reasonable guess based on the current grid?

Don't forget your goal is to maximize your score by making the best guess with the least number of observations to get the highest score.

Remember: Only use symbols from: {symbols_str}
Row/Col indices: 0 to {grid_size - 1}

What is your next move?"""

    return prompt


def format_grid_for_display(grid: List[List[str]], grid_size: int) -> str:
    """将网格格式化为易读的显示格式"""
    if not grid or len(grid) == 0:
        return "Empty grid"

    # 创建列标题
    header = "    " + "   ".join(chr(65 + i) for i in range(grid_size))
    lines = [header]

    # 添加分隔线
    separator = "  " + "---" * grid_size + "-" * (grid_size - 1)
    lines.append(separator)

    # 添加每一行
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
            row_str = f"{i + 1} |" + "|".join(row_data) + "|"
        else:
            row_str = f"{i + 1} |" + "|".join([" ? "] * grid_size) + "|"
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
You must return a valid JSON object with this **exact structure**:
{{
  "pattern": [
    ["", "", ...],
    ["", "", ...],
    ...
  ],
  "description": "Brief explanation of the underlying pattern logic and design inspiration."
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
        api_params = build_openai_params(request.llmModelParams)
        # 对于设计模式，使用稍高的temperature以增加创造性
        if "temperature" not in api_params or api_params["temperature"] < 0.5:
            api_params["temperature"] = 0.7

        response = await openai_client.chat.completions.create(
            model=request.llmModel or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            **api_params
        )
    else:
        if not together_client:
            raise Exception("Together.ai API客户端未初始化，请检查TOGETHER_API_KEY环境变量")

        # 构建Together.ai API参数
        api_params = build_together_params(request.llmModelParams)
        # 对于设计模式，使用稍高的temperature以增加创造性
        if "temperature" not in api_params or api_params["temperature"] < 0.5:
            api_params["temperature"] = 0.7

        response = await asyncio.to_thread(
            together_client.chat.completions.create,
            model=request.llmModel or "deepseek-ai/DeepSeek-V3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            **api_params
        )

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


async def llm_player_turn_logic(request: LLMPlayerTurnRequest) -> LLMPlayerTurnResponse:
    """LLM玩家回合逻辑 - 从端点中提取出来供后台任务使用"""
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

    # 判断使用哪个API
    if is_openai_model(request.llmModel):
        if not openai_client:
            raise Exception("OpenAI API客户端未初始化，请检查OPENAI_API_KEY环境变量")

        # 构建OpenAI API参数
        api_params = build_openai_params(request.llmModelParams)

        # 调用OpenAI API
        response = await openai_client.chat.completions.create(
            model=request.llmModel or "gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            **api_params
        )
    else:
        if not together_client:
            raise Exception("Together.ai API客户端未初始化，请检查TOGETHER_API_KEY环境变量")

        # 构建Together.ai API参数
        api_params = build_together_params(request.llmModelParams)

        # 调用Together.ai API
        response = await asyncio.to_thread(
            together_client.chat.completions.create,
            model=request.llmModel or "deepseek-ai/DeepSeek-V3",
            messages=messages,
            response_format={"type": "json_object"},
            **api_params
        )

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

    return parsed_response


async def save_game_logic(request: GameCreateRequest) -> GameCreateResponse:
    """保存游戏逻辑 - 从端点中提取出来供后台任务使用"""
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

        for player in request.players:
            # 安全地处理 queried_cells
            queried_cells_json = None
            if player.queried_cells:
                try:
                    queried_cells_json = json.dumps([{"row": p.row, "col": p.col} for p in player.queried_cells])
                except Exception as e:
                    logger.warning(f"Failed to serialize queried_cells for player {player.player_name_in_game}: {e}")
                    queried_cells_json = None

            # 序列化玩家模型参数
            player_params_json = None
            if player.player_llm_model_params:
                player_params_json = json.dumps(player.player_llm_model_params.dict(exclude_none=True))

            await conn.execute(
                """
                INSERT INTO game_players (game_id, player_name_in_game, player_type, player_llm_model,
                                          player_llm_model_params, final_score, final_guess, action_log, queried_cells)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                game_id, player.player_name_in_game, player.player_type,
                player.player_llm_model, player_params_json, player.final_score,
                json.dumps(player.final_guess) if player.final_guess else None,
                json.dumps(player.action_log) if player.action_log else None,
                queried_cells_json
            )

        logger.info(f"Game saved successfully with ID: {game_id}")
        return GameCreateResponse(message="Game saved successfully", game_id=game_id)


# --- 获取模型列表的逻辑函数 ---

async def get_available_models_logic() -> ModelsListResponse:
    """获取可用的模型列表的核心逻辑 - 合并Together.ai和OpenAI模型"""
    logger.info("正在获取模型列表...")

    all_models = []

    # 获取Together.ai模型
    if together_client:
        try:
            logger.info("正在从Together.ai API获取模型列表...")
            models_response = await asyncio.to_thread(together_client.models.list)
            logger.info(f"Together.ai API返回了模型数据，类型: {type(models_response)}")

            # 处理不同的响应格式
            if hasattr(models_response, 'data'):
                models_data = models_response.data
                logger.info(f"从data属性获取到 {len(models_data)} 个Together.ai模型")
            elif isinstance(models_response, list):
                models_data = models_response
                logger.info(f"直接从列表获取到 {len(models_data)} 个Together.ai模型")
            else:
                models_data = list(models_response) if models_response else []
                logger.info(f"转换后获取到 {len(models_data)} 个Together.ai模型")

            # 需要排除的特定模型列表
            exclude_models = [
                'meta-llama/Llama-Vision-Free',
                'meta-llama/Llama-Guard-3-8B',
                'microsoft/DialoGPT-medium',
                'togethercomputer/RedPajama-INCITE-Base-3B-v1',
                'togethercomputer/RedPajama-INCITE-Instruct-3B-v1',
            ]

            # 过滤出聊天模型
            for model in models_data:
                try:
                    model_id = getattr(model, 'id', str(model))
                    model_type = getattr(model, 'type', '').lower()

                    # 只包含聊天模型，并排除特定模型
                    if model_type == 'chat' and model_id not in exclude_models:
                        # 安全地处理pricing字段
                        pricing_data = getattr(model, 'pricing', None)
                        if pricing_data is not None and hasattr(pricing_data, '__dict__'):
                            try:
                                pricing_dict = pricing_data.__dict__ if hasattr(pricing_data, '__dict__') else None
                            except:
                                pricing_dict = None
                        else:
                            pricing_dict = pricing_data

                        all_models.append(TogetherModel(
                            id=model_id,
                            object="model",
                            created=getattr(model, 'created', 0),
                            owned_by=getattr(model, 'owned_by', 'together'),
                            type=getattr(model, 'type', None),
                            display_name=getattr(model, 'display_name', None),
                            organization=getattr(model, 'organization', None),
                            context_length=getattr(model, 'context_length', None),
                            link=getattr(model, 'link', None),
                            license=getattr(model, 'license', None),
                            pricing=pricing_dict
                        ))
                except Exception as e:
                    logger.warning(f"跳过Together.ai模型 {getattr(model, 'id', 'unknown')} 由于错误: {e}")
                    continue

        except Exception as e:
            logger.error(f"获取Together.ai模型列表失败: {str(e)}")

    # 添加OpenAI模型 - 使用缓存的模型列表
    with openai_models_cache_lock:
        cached_openai_models = openai_models_cache.copy()

    for model_id in cached_openai_models:
        all_models.append(TogetherModel(
            id=model_id,
            object="model",
            created=0,
            owned_by="openai",
            type="chat",
            display_name=model_id,
            organization="openai",
            context_length=None,
            link=None,
            license=None,
            pricing=None
        ))

    logger.info(f"从缓存添加了 {len(cached_openai_models)} 个OpenAI模型")

    # 按模型名称排序，优先显示常用模型，默认模型为deepseek-ai/DeepSeek-V3
    priority_models = [
        'deepseek-ai/DeepSeek-V3',
        'meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo',
        'meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo',
        'meta-llama/Llama-3.2-3B-Instruct-Turbo',
        'meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo',
        'Qwen/Qwen2.5-7B-Instruct-Turbo',
        'Qwen/Qwen2.5-72B-Instruct-Turbo',
        'mistralai/Mixtral-8x7B-Instruct-v0.1',
        'google/gemma-2-9b-it',
        'chatgpt-4o-latest',
        'gpt-4o',
        'gpt-4o-mini',
        'gpt-4',
        'gpt-4-turbo',
        'gpt-3.5-turbo',
        'o1-preview',
        'o1-mini'
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
            TogetherModel(id="deepseek-ai/DeepSeek-V3", object="model", created=0, owned_by="together"),
            TogetherModel(id="gpt-4o-mini", object="model", created=0, owned_by="openai"),
            TogetherModel(id="gpt-4o", object="model", created=0, owned_by="openai"),
            TogetherModel(id="chatgpt-4o-latest", object="model", created=0, owned_by="openai"),
            TogetherModel(id="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", object="model", created=0,
                          owned_by="together"),
            TogetherModel(id="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", object="model", created=0,
                          owned_by="together"),
            TogetherModel(id="Qwen/Qwen2.5-7B-Instruct-Turbo", object="model", created=0, owned_by="together"),
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


# --- API端点实现 ---

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
        return await llm_player_turn_logic(request)

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
        return await design_pattern_logic(request)

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
    """获取特定游戏的详细信息"""
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
                'players': players
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
async def get_leaderboard_endpoint(test_set_id: Optional[str] = None):
    """获取排行榜"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        calculator = LeaderboardCalculator(db_pool)
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
    together_status = "configured" if together_client else "not configured"
    openai_status = "configured" if openai_client else "not configured"

    return {
        "status": "healthy",
        "together_configured": bool(together_client),
        "openai_configured": bool(openai_client),
        "database_connected": bool(db_pool),
        "database_status": db_status,
        "together_status": together_status,
        "openai_status": openai_status
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
