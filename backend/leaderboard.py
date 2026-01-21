import math
import json
import logging
import uuid
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict
import asyncpg
import trueskill
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)

# Pricing tables for different models (per 1M tokens)
OPENAI_PRICING = {
    # Standard models
    'gpt-5': {'input': 1.25, 'output': 10.00},
    'gpt-5-mini': {'input': 0.25, 'output': 2.00},
    'gpt-5-nano': {'input': 0.05, 'output': 0.40},
    'gpt-5-chat-latest': {'input': 1.25, 'output': 10.00},
    'gpt-4.1': {'input': 2.00, 'output': 8.00},
    'gpt-4.1-mini': {'input': 0.40, 'output': 1.60},
    'gpt-4.1-nano': {'input': 0.10, 'output': 0.40},
    'gpt-4o': {'input': 2.50, 'output': 10.00},
    'gpt-4o-2024-05-13': {'input': 5.00, 'output': 15.00},
    'gpt-4o-mini': {'input': 0.15, 'output': 0.60},
    'gpt-realtime': {'input': 4.00, 'output': 16.00},
    'gpt-4o-realtime-preview': {'input': 5.00, 'output': 20.00},
    'gpt-4o-mini-realtime-preview': {'input': 0.60, 'output': 2.40},
    'gpt-audio': {'input': 2.50, 'output': 10.00},
    'gpt-4o-audio-preview': {'input': 2.50, 'output': 10.00},
    'gpt-4o-mini-audio-preview': {'input': 0.15, 'output': 0.60},
    'o1': {'input': 15.00, 'output': 60.00},
    'o1-pro': {'input': 150.00, 'output': 600.00},
    'o3-pro': {'input': 20.00, 'output': 80.00},
    'o3': {'input': 2.00, 'output': 8.00},
    'o3-deep-research': {'input': 10.00, 'output': 40.00},
    'o4-mini': {'input': 1.10, 'output': 4.40},
    'o4-mini-deep-research': {'input': 2.00, 'output': 8.00},
    'o3-mini': {'input': 1.10, 'output': 4.40},
    'o1-mini': {'input': 1.10, 'output': 4.40},
    'codex-mini-latest': {'input': 1.50, 'output': 6.00},
    'gpt-4o-mini-search-preview': {'input': 0.15, 'output': 0.60},
    'gpt-4o-search-preview': {'input': 2.50, 'output': 10.00},
    'computer-use-preview': {'input': 3.00, 'output': 12.00},
    'gpt-image-1': {'input': 5.00, 'output': 0.00},
    # Common aliases
    'chatgpt-4o-latest': {'input': 2.50, 'output': 10.00},
    'gpt-4-turbo': {'input': 10.00, 'output': 30.00},
    'gpt-3.5-turbo': {'input': 0.50, 'output': 1.50},
    'gpt-4': {'input': 30.00, 'output': 60.00},
    'gpt-4-turbo-preview': {'input': 10.00, 'output': 30.00},
    'gpt-4-0125-preview': {'input': 10.00, 'output': 30.00},
    'gpt-4-1106-preview': {'input': 10.00, 'output': 30.00},
    'gpt-3.5-turbo-0125': {'input': 0.50, 'output': 1.50},
    'gpt-3.5-turbo-1106': {'input': 1.00, 'output': 2.00},
    'o1-preview': {'input': 15.00, 'output': 60.00},
}

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
    """Get pricing for a model with enhanced logging"""
    logger.debug(f"查找模型 {model_name} 的定价信息")

    # Remove openai_official/ prefix for OpenAI models
    clean_model_name = model_name.replace("openai_official/", "")

    # Check OpenAI pricing first
    if clean_model_name in OPENAI_PRICING:
        pricing = OPENAI_PRICING[clean_model_name]
        logger.debug(
            f"找到 OpenAI 模型 {clean_model_name} 的定价: 输入 ${pricing['input']}/1M, 输出 ${pricing['output']}/1M")
        return pricing

    # Check OpenRouter pricing
    if model_name in OPENROUTER_PRICING:
        pricing = OPENROUTER_PRICING[model_name]
        logger.debug(
            f"找到 OpenRouter 模型 {model_name} 的定价: 输入 ${pricing['input']}/1M, 输出 ${pricing['output']}/1M")
        return pricing

    # Default pricing for unknown models (rough estimate)
    logger.warning(f"没有找到模型 {model_name} 的定价，使用默认定价")
    return {'input': 1.00, 'output': 3.00}


def calculate_cost(input_tokens: int, output_tokens: int, model_name: str) -> float:
    """Calculate cost for a model based on token usage with enhanced logging"""
    logger.debug(f"计算模型 {model_name} 的成本: 输入 {input_tokens} tokens, 输出 {output_tokens} tokens")

    pricing = get_model_pricing(model_name)
    if not pricing:
        logger.warning(f"无法获取模型 {model_name} 的定价信息")
        return 0.0

    # Convert to cost (pricing is per 1M tokens)
    input_cost = (input_tokens / 1_000_000) * pricing['input']
    output_cost = (output_tokens / 1_000_000) * pricing['output']
    total_cost = input_cost + output_cost

    logger.debug(
        f"模型 {model_name} 成本计算: 输入成本 ${input_cost:.6f}, 输出成本 ${output_cost:.6f}, 总成本 ${total_cost:.6f}")

    return total_cost


@dataclass
class PlayerStats:
    """玩家统计数据"""
    model_name: str
    model_params: Optional[Dict[str, Any]] = None
    games_as_player: int = 0
    games_as_designer: int = 0
    total_score_as_player: int = 0
    total_score_as_designer: int = 0  # This will now store the old formula designer score
    total_in_game_designer_score: float = 0.0
    total_meta_designer_score: float = 0.0
    wins_as_player: int = 0
    wins_as_designer: int = 0
    inter_designer_wins: int = 0  # Added inter-designer wins tracking
    overall_wins: int = 0  # 总体胜利次数（在单局游戏中得分最高）
    total_games: int = 0  # 总游戏次数（包括作为玩家和设计师）
    elo_rating: float = 1500.0
    trueskill_rating: Optional[trueskill.Rating] = None
    total_cost: float = 0.0  # 总成本
    total_input_tokens: int = 0  # 总输入token数
    total_output_tokens: int = 0  # 总输出token数

    def __post_init__(self):
        if self.trueskill_rating is None:
            # 使用官方TrueSkill库创建默认评分
            env = trueskill.TrueSkill(draw_probability=0.1)
            self.trueskill_rating = env.create_rating()

    @property
    def avg_score_as_player(self) -> float:
        return self.total_score_as_player / self.games_as_player if self.games_as_player > 0 else 0.0

    @property
    def avg_score_as_designer(self) -> float:
        return self.total_score_as_designer / self.games_as_designer if self.games_as_designer > 0 else 0.0

    @property
    def avg_in_game_designer_score(self) -> float:
        return self.total_in_game_designer_score / self.games_as_designer if self.games_as_designer > 0 else 0.0

    @property
    def avg_meta_designer_score(self) -> float:
        return self.total_meta_designer_score / self.games_as_designer if self.games_as_designer > 0 else 0.0

    @property
    def win_rate_as_player(self) -> float:
        return (self.wins_as_player / self.games_as_player * 100) if self.games_as_player > 0 else 0.0

    @property
    def win_rate_as_designer(self) -> float:
        return (self.wins_as_designer / self.games_as_designer * 100) if self.games_as_designer > 0 else 0.0

    @property
    def inter_designer_win_rate(self) -> float:
        """轮内设计师胜率：在同一轮中比其他设计师得分高的比率"""
        return (self.inter_designer_wins / self.games_as_designer * 100) if self.games_as_designer > 0 else 0.0

    @property
    def overall_win_rate(self) -> float:
        """总体胜率：在所有参与的游戏中获得最高分的比率"""
        return (self.overall_wins / self.total_games * 100) if self.total_games > 0 else 0.0

    @property
    def conservative_trueskill_rating(self) -> float:
        """TrueSkill保守评分：μ - 3σ"""
        if self.trueskill_rating:
            return self.trueskill_rating.mu - 3 * self.trueskill_rating.sigma
        return 0.0

    @property
    def cost_per_game(self) -> float:
        """每局游戏的平均成本"""
        return self.total_cost / self.total_games if self.total_games > 0 else 0.0


def designer_percentile(scores: List[float], grid_size: Tuple[int, int],
                        a: float = 7.0, b: float = 3.0, m0: float = 0.40,
                        s0: float = 0.30, sigma_ref: float = 4.0) -> float:
    """Calculate designer percentile based on player scores"""
    H, W = grid_size
    Smax = H * W
    scores_array = np.asarray(scores, dtype=float)
    mu, sigma = scores_array.mean(), scores_array.std()

    m = mu / Smax
    s = min(sigma, Smax / 2) / (Smax / 2)

    z = a * (m0 - m) + b * (s - s0)
    p = 1.0 / (1.0 + np.exp(-z))

    kappa = min(1.0, sigma / sigma_ref)
    p_prime = 0.5 + kappa * (p - 0.5)

    return float(p_prime)


def designer_in_game_score(scores: List[float], p_prime: float,
                           bump_top: bool = False, eps: float = 1e-6) -> float:
    """Calculate in-game designer score"""
    scores_array = np.sort(np.asarray(scores, dtype=float))
    n = len(scores_array)
    pis = (np.arange(1, n + 1) - 0.5) / n
    val = float(np.interp(p_prime, pis, scores_array))

    if bump_top and p_prime >= pis[-1]:
        val = scores_array[-1] + eps

    return val


def designer_meta_score(p_prime: float, grid_size: Tuple[int, int]) -> float:
    """Calculate meta designer score"""
    H, W = grid_size
    return (H * W) * p_prime


class ELOCalculator:
    """ELO评分计算器"""

    @staticmethod
    def expected_score(rating_a: float, rating_b: float) -> float:
        """计算期望得分"""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))

    @staticmethod
    def update_rating(rating: float, expected: float, actual: float, k_factor: float = 32) -> float:
        """更新ELO评分"""
        return rating + k_factor * (actual - expected)

    @classmethod
    def calculate_k_factor(cls, num_players: int) -> float:
        """根据玩家数量动态计算K因子：K = 32 / log(N+1)"""
        if num_players <= 1:
            return 32.0
        return 32.0 / math.log(num_players + 1)

    @classmethod
    def update_ratings_multiplayer(cls, players: List[Tuple[str, float, int]]) -> Dict[str, float]:
        """
        多人游戏ELO评分更新
        players: [(player_id, current_rating, final_score), ...]
        返回: {player_id: new_rating, ...}
        """
        if len(players) < 2:
            return {player_id: rating for player_id, rating, _ in players}

        # 动态计算K因子
        k_factor = cls.calculate_k_factor(len(players))

        # 按分数排序，分数高的排名靠前
        sorted_players = sorted(players, key=lambda x: x[2], reverse=True)
        n = len(players)
        new_ratings = {}

        for i, (player_id, rating, score) in enumerate(sorted_players):
            total_expected = 0.0
            actual_score = 0.0

            # 计算与其他所有玩家的期望得分和实际得分
            for j, (other_id, other_rating, other_score) in enumerate(sorted_players):
                if i != j:
                    expected = cls.expected_score(rating, other_rating)
                    total_expected += expected

                    # 实际得分：赢=1，平=0.5，输=0
                    if score > other_score:
                        actual_score += 1.0
                    elif score == other_score:
                        actual_score += 0.5

            # 标准化到0-1范围
            if n > 1:
                total_expected /= (n - 1)
                actual_score /= (n - 1)

            new_rating = cls.update_rating(rating, total_expected, actual_score, k_factor)
            new_ratings[player_id] = max(100, new_rating)  # 最低评分100

        return new_ratings


class TrueSkillCalculator:
    """TrueSkill评分计算器（使用官方库）"""

    def __init__(self):
        # 创建TrueSkill环境
        self.env = trueskill.TrueSkill(draw_probability=0.1)

    def create_rating(self) -> trueskill.Rating:
        """创建新的评分"""
        return self.env.create_rating()

    def update_ratings_multiplayer(self, players: List[Tuple[str, trueskill.Rating, int]]) -> Dict[
        str, trueskill.Rating]:
        """
        多人游戏TrueSkill评分更新
        players: [(player_id, current_rating, final_score), ...]
        返回: {player_id: new_rating, ...}
        """
        if len(players) < 2:
            return {player_id: rating for player_id, rating, _ in players}

        # 按分数排序，创建排名
        sorted_players = sorted(players, key=lambda x: x[2], reverse=True)

        # 创建排名列表（分数相同的玩家排名相同）
        ranks = []
        current_rank = 0
        prev_score = None

        for i, (player_id, rating, score) in enumerate(sorted_players):
            if prev_score is None or score != prev_score:
                current_rank = i
            ranks.append(current_rank)
            prev_score = score

        # 创建rating_groups - 每个玩家作为单独的团队
        rating_groups = [[rating] for _, rating, _ in sorted_players]

        # 使用TrueSkill更新评分
        new_rating_groups = self.env.rate(rating_groups, ranks=ranks)

        # 构建结果字典
        new_ratings = {}
        for i, (player_id, _, _) in enumerate(sorted_players):
            new_ratings[player_id] = new_rating_groups[i][0]  # 取出团队中的唯一评分

        return new_ratings


class LeaderboardCalculator:
    """排行榜计算器"""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.elo_calculator = ELOCalculator()
        self.trueskill_calculator = TrueSkillCalculator()

    async def calculate_leaderboard(self, test_set_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        计算排行榜
        test_set_id: 如果提供，只计算特定测试集的数据；否则计算所有数据
        """
        try:
            logger.info(f"开始计算排行榜，test_set_id: {test_set_id}")

            # 获取游戏数据
            games_data = await self._fetch_games_data(test_set_id)

            if not games_data:
                logger.warning("没有找到游戏数据")
                return []

            logger.info(f"获取到 {len(games_data)} 条游戏记录")

            # 初始化玩家统计
            player_stats = defaultdict(lambda: PlayerStats(""))

            # 收集基础统计数据
            await self._collect_basic_stats(games_data, player_stats)

            # 计算ELO和TrueSkill评分
            await self._calculate_ratings(games_data, player_stats)

            # 转换为排行榜格式
            leaderboard = self._format_leaderboard(player_stats)

            # 保存排行榜数据到数据库
            await self._save_leaderboard_to_db(leaderboard, test_set_id)

            logger.info(f"成功计算排行榜，共 {len(leaderboard)} 个条目")
            return leaderboard

        except Exception as e:
            logger.error(f"计算排行榜失败: {e}", exc_info=True)
            return []

    async def _fetch_games_data(self, test_set_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取游戏数据，包括token使用情况和设计师分数"""
        try:
            async with self.db_pool.acquire() as conn:
                # 首先检查数据库中是否有游戏数据
                total_games_query = "SELECT COUNT(*) FROM games"
                total_games = await conn.fetchval(total_games_query)
                logger.info(f"数据库中总共有 {total_games} 个游戏")

                if total_games == 0:
                    logger.warning("数据库中没有游戏数据")
                    return []

                # 检查有多少LLM玩家
                llm_players_query = "SELECT COUNT(*) FROM game_players WHERE player_type = 'LLM'"
                llm_players_count = await conn.fetchval(llm_players_query)
                logger.info(f"数据库中有 {llm_players_count} 个LLM玩家记录")

                # 检查token数据的情况
                token_data_query = "SELECT COUNT(*) FROM game_players WHERE player_type = 'LLM' AND (input_tokens > 0 OR output_tokens > 0)"
                token_data_count = await conn.fetchval(token_data_query)
                logger.info(f"数据库中有 {token_data_count} 个LLM玩家记录包含token数据")

                if test_set_id:
                    query = """
                            SELECT g.id as game_id, \
                                   g.designer_type, \
                                   g.designer_llm_model, \
                                   g.designer_llm_model_params, \
                                   g.created_at, \
                                   gp.player_name_in_game, \
                                   gp.player_type, \
                                   gp.player_llm_model, \
                                   gp.player_llm_model_params, \
                                   gp.final_score, \
                                   gp.input_tokens, \
                                   gp.output_tokens, \
                                   ga.in_game_designer_score, \
                                   ga.meta_designer_score
                            FROM games g
                                     JOIN game_players gp ON g.id = gp.game_id
                                     LEFT JOIN game_analytics ga \
                                               ON g.id = ga.game_id AND ga.participant_role = 'designer'
                            WHERE g.test_set_id = $1 \
                              AND gp.player_type = 'LLM'
                            ORDER BY g.created_at, g.id \
                            """
                    rows = await conn.fetch(query, test_set_id)
                    logger.info(f"测试集 {test_set_id} 查询到 {len(rows)} 条记录")
                else:
                    query = """
                            SELECT g.id as game_id, \
                                   g.designer_type, \
                                   g.designer_llm_model, \
                                   g.designer_llm_model_params, \
                                   g.created_at, \
                                   gp.player_name_in_game, \
                                   gp.player_type, \
                                   gp.player_llm_model, \
                                   gp.player_llm_model_params, \
                                   gp.final_score, \
                                   gp.input_tokens, \
                                   gp.output_tokens, \
                                   ga.in_game_designer_score, \
                                   ga.meta_designer_score
                            FROM games g
                                     JOIN game_players gp ON g.id = gp.game_id
                                     LEFT JOIN game_analytics ga \
                                               ON g.id = ga.game_id AND ga.participant_role = 'designer'
                            WHERE gp.player_type = 'LLM'
                            ORDER BY g.created_at, g.id \
                            """
                    rows = await conn.fetch(query)
                    logger.info(f"全部游戏查询到 {len(rows)} 条记录")

                if not rows:
                    logger.warning("没有找到LLM玩家的游戏记录")
                    return []

                # 按游戏ID分组
                games_by_id = defaultdict(list)
                for row in rows:
                    game_id = str(row['game_id'])

                    # 解析玩家模型参数
                    player_params = None
                    if row['player_llm_model_params']:
                        try:
                            if isinstance(row['player_llm_model_params'], str):
                                player_params = json.loads(row['player_llm_model_params'])
                            else:
                                player_params = row['player_llm_model_params']
                        except Exception as e:
                            logger.warning(f"解析玩家参数失败: {e}")
                            player_params = None

                    # 确保token数据不为None
                    input_tokens = row.get('input_tokens') or 0
                    output_tokens = row.get('output_tokens') or 0

                    player_data = {
                        'player_name_in_game': row['player_name_in_game'],
                        'player_type': row['player_type'],
                        'player_llm_model': row['player_llm_model'],
                        'player_llm_model_params': player_params,
                        'final_score': row['final_score'] if row['final_score'] is not None else 0,
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens
                    }

                    # 如果这是第一个玩家，添加游戏信息
                    if game_id not in games_by_id:
                        # 解析设计师模型参数
                        designer_params = None
                        if row['designer_llm_model_params']:
                            try:
                                if isinstance(row['designer_llm_model_params'], str):
                                    designer_params = json.loads(row['designer_llm_model_params'])
                                else:
                                    designer_params = row['designer_llm_model_params']
                            except Exception as e:
                                logger.warning(f"解析设计师参数失败: {e}")
                                designer_params = None

                        games_by_id[game_id] = {
                            'id': game_id,
                            'designer_type': row['designer_type'],
                            'designer_llm_model': row['designer_llm_model'],
                            'designer_llm_model_params': designer_params,
                            'created_at': row['created_at'],
                            'in_game_designer_score': float(row['in_game_designer_score']) if row[
                                                                                                  'in_game_designer_score'] is not None else None,
                            'meta_designer_score': float(row['meta_designer_score']) if row[
                                                                                            'meta_designer_score'] is not None else None,
                            'players': []
                        }

                    games_by_id[game_id]['players'].append(player_data)

                # 转换为列表
                games_data = list(games_by_id.values())

                logger.info(f"成功处理 {len(games_data)} 个游戏")

                # 打印一些调试信息
                for i, game in enumerate(games_data[:3]):  # 只打印前3个游戏的信息
                    logger.info(f"游戏 {i + 1}: {len(game['players'])} 个玩家, 设计师: {game['designer_type']}")
                    for j, player in enumerate(game['players']):
                        logger.info(
                            f"  玩家 {j + 1}: {player['player_llm_model']}, 分数: {player['final_score']}, tokens: {player['input_tokens']}/{player['output_tokens']}")

                return games_data

        except Exception as e:
            logger.error(f"获取游戏数据失败: {e}", exc_info=True)
            return []

    async def _collect_basic_stats(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """收集基础统计数据 - 从game_analytics读取设计师分数"""
        logger.info("开始收集基础统计数据")

        designer_wins_from_analytics = await self._fetch_designer_wins_from_analytics()

        for game in games_data:
            try:
                # 收集该局游戏中所有参与者的得分
                all_participants_scores = []  # Stores (participant_key, score, type)
                designer_key = None
                designer_score = 0

                # 处理设计师 - 从game_analytics读取分数
                if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                    designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])
                    if designer_key not in player_stats:
                        player_stats[designer_key] = PlayerStats(
                            model_name=game['designer_llm_model'],
                            model_params=game['designer_llm_model_params']
                        )

                    in_game_score = game.get('in_game_designer_score')
                    meta_score = game.get('meta_designer_score')

                    player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                    old_formula_designer_score = 0
                    if len(player_scores) >= 2:
                        max_score = max(player_scores)
                        min_score = min(player_scores)
                        # designer score = 2 * (clip(max_score, 0) - clip(min_score, 0))
                        old_formula_designer_score = 2 * (max(max_score, 0) - max(min_score, 0))

                    # If scores are not in game_analytics, calculate them
                    if in_game_score is None or meta_score is None:
                        if len(player_scores) >= 2:
                            # Assume square grid (can be extended)
                            grid_size = (6, 6)  # Default, should be read from game data

                            p_prime = designer_percentile(player_scores, grid_size)
                            in_game_score = designer_in_game_score(player_scores, p_prime, bump_top=True)
                            meta_score = designer_meta_score(p_prime, grid_size)
                            logger.warning(f"游戏 {game['id']}: 设计师分数未在game_analytics中找到，重新计算")

                    if in_game_score is not None and meta_score is not None:
                        player_stats[designer_key].games_as_designer += 1
                        player_stats[designer_key].total_score_as_designer += old_formula_designer_score
                        player_stats[designer_key].total_in_game_designer_score += in_game_score
                        player_stats[designer_key].total_meta_designer_score += meta_score
                        player_stats[designer_key].total_games += 1

                        all_participants_scores.append((designer_key, in_game_score, 'designer'))
                        designer_score = in_game_score

                        logger.debug(
                            f"游戏 {game['id']}: 设计师 {designer_key} 旧公式分数 {old_formula_designer_score:.2f}, In-game分数 {in_game_score:.2f}, Meta分数 {meta_score:.2f}")

                # 处理玩家 - 增强版本，确保正确计算成本
                valid_players_for_win_rate = []  # Stores (player_key, final_score) for win rate calculation
                for player in game['players']:
                    if player['player_type'] == 'LLM' and player['player_llm_model'] and player[
                        'final_score'] is not None:
                        player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])

                        if player_key not in player_stats:
                            player_stats[player_key] = PlayerStats(
                                model_name=player['player_llm_model'],
                                model_params=player['player_llm_model_params']
                            )

                        player_stats[player_key].games_as_player += 1
                        player_stats[player_key].total_score_as_player += player['final_score']
                        player_stats[player_key].total_games += 1

                        # 获取token数据并计算成本
                        input_tokens = player.get('input_tokens', 0) or 0
                        output_tokens = player.get('output_tokens', 0) or 0

                        logger.debug(f"玩家 {player_key} token数据: 输入 {input_tokens}, 输出 {output_tokens}")

                        # 计算成本 - 即使token为0也要调用，以便记录日志
                        player_cost = calculate_cost(input_tokens, output_tokens, player['player_llm_model'])

                        player_stats[player_key].total_input_tokens += input_tokens
                        player_stats[player_key].total_output_tokens += output_tokens
                        player_stats[player_key].total_cost += player_cost

                        if player_cost > 0:
                            logger.info(
                                f"游戏 {game['id']}: 玩家 {player_key} 得分 {player['final_score']}, tokens: {input_tokens}/{output_tokens}, 成本 ${player_cost:.6f}")
                        else:
                            logger.warning(
                                f"游戏 {game['id']}: 玩家 {player_key} 得分 {player['final_score']}, tokens: {input_tokens}/{output_tokens}, 成本计算为0")

                        # 添加到有效玩家列表，用于计算玩家胜率
                        valid_players_for_win_rate.append((player_key, player['final_score']))
                        all_participants_scores.append((player_key, player['final_score'], 'player'))

                # Calculate player wins
                if valid_players_for_win_rate:
                    max_player_score = max(score for _, score in valid_players_for_win_rate)
                    # 找出所有得分最高的玩家（可能有平局）
                    winners = [player_key for player_key, score in valid_players_for_win_rate if
                               score == max_player_score]

                    for winner_key in winners:
                        player_stats[winner_key].wins_as_player += 1
                        logger.debug(f"游戏 {game['id']}: 玩家获胜 {winner_key} 得分 {max_player_score}")

                # Check if designer won using game_analytics data
                if designer_key and game['id'] in designer_wins_from_analytics:
                    analytics_data = designer_wins_from_analytics[game['id']]
                    if analytics_data.get('rank_in_game_incl_designer') == 1:
                        player_stats[designer_key].wins_as_designer += 1
                        logger.debug(f"游戏 {game['id']}: 设计师获胜 {designer_key} (rank=1 from analytics)")

                # Calculate overall wins (highest score among all participants)
                if all_participants_scores:
                    max_overall_score = max(score for _, score, _ in all_participants_scores)
                    winners = [participant_key for participant_key, score, _ in all_participants_scores if
                               score == max_overall_score]

                    logger.debug(f"游戏 {game['id']}: 最高分 {max_overall_score}, 获胜者 {winners}")

                    for participant_key in winners:
                        player_stats[participant_key].overall_wins += 1
                        logger.debug(f"游戏 {game['id']}: 总体获胜 {participant_key} 得分 {max_overall_score}")

            except Exception as e:
                logger.error(f"处理游戏 {game.get('id', 'unknown')} 数据失败: {e}", exc_info=True)
                continue

        games_by_round = self._group_games_by_round(games_data)

        for round_games in games_by_round:
            try:
                # 收集这一轮中所有设计师的Meta Designer Score
                round_designers = []

                for game in round_games:
                    if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                        designer_key = self._get_player_key(game['designer_llm_model'],
                                                            game['designer_llm_model_params'])

                        # 使用Meta Designer Score而不是In-game Designer Score
                        meta_score = game.get('meta_designer_score')

                        # 如果meta_score不存在，重新计算
                        if meta_score is None:
                            player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                            if len(player_scores) >= 2:
                                grid_size = (6, 6)  # Default, should be read from game data
                                p_prime = designer_percentile(player_scores, grid_size)
                                meta_score = designer_meta_score(p_prime, grid_size)
                                logger.warning(f"游戏 {game['id']}: Meta Designer Score未找到，重新计算")

                        if meta_score is not None:
                            round_designers.append((designer_key, meta_score))

                # 计算轮内设计师胜率（基于Meta Designer Score比较所有设计师）
                if len(round_designers) > 1:  # Only compare if there are multiple designers
                    max_meta_score = max(score for _, score in round_designers)
                    winners = [designer_key for designer_key, score in round_designers if score == max_meta_score]

                    for winner_key in winners:
                        player_stats[winner_key].inter_designer_wins += 1
                        logger.debug(f"轮内设计师获胜 {winner_key}: Meta分数 {max_meta_score} (轮内最高)")

            except Exception as e:
                logger.error(f"计算轮内设计师胜率失败: {e}", exc_info=True)
                continue

        # 打印统计结果
        logger.info(f"统计完成，共 {len(player_stats)} 个模型:")
        for key, stats in player_stats.items():
            logger.info(
                f"  {key}: 玩家游戏{stats.games_as_player}局(胜{stats.wins_as_player}), 设计师游戏{stats.games_as_designer}局(胜{stats.wins_as_designer}, 轮内胜{stats.inter_designer_wins}), 总体胜{stats.overall_wins}/{stats.total_games}, tokens: {stats.total_input_tokens}/{stats.total_output_tokens}, 成本${stats.total_cost:.6f}")

    def _group_games_by_round(self, games_data: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        将游戏按轮换分组
        假设同一轮换的游戏在时间上相近，且设计师模型相同
        """
        if not games_data:
            return []

        # 按时间排序
        sorted_games = sorted(games_data, key=lambda x: x['created_at'])

        rounds = []
        current_round = []
        current_designers = set()

        for game in sorted_games:
            designer_key = None
            if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])

            # 如果当前轮换为空，或者设计师已经在当前轮换中出现过，开始新轮换
            if not current_round or (designer_key and designer_key in current_designers):
                if current_round:
                    rounds.append(current_round)
                current_round = [game]
                current_designers = {designer_key} if designer_key else set()
            else:
                current_round.append(game)
                if designer_key:
                    current_designers.add(designer_key)

        # 添加最后一轮
        if current_round:
            rounds.append(current_round)

        logger.info(f"游戏分组完成，共 {len(rounds)} 轮，每轮游戏数: {[len(r) for r in rounds]}")
        return rounds

    async def _calculate_ratings(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """计算ELO和TrueSkill评分"""
        logger.info("开始计算评分")

        # 按时间顺序处理游戏
        games_processed = 0
        for game in games_data:
            try:
                # 收集所有参与者（设计师 + 玩家）
                all_participants_for_rating = []  # Stores (participant_key, score, type)

                # 添加设计师
                designer_key = None
                if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                    designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])
                    if designer_key in player_stats:
                        # Calculate designer score with clipping
                        player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                        if len(player_scores) >= 2:
                            # Assume square grid (can be extended)
                            grid_size = (6, 6)  # Default, should be read from game data

                            p_prime = designer_percentile(player_scores, grid_size)
                            in_game_score = designer_in_game_score(player_scores, p_prime, bump_top=True)
                            all_participants_for_rating.append((designer_key, in_game_score, 'designer'))

                # 添加玩家
                for player in game['players']:
                    if (player['player_type'] == 'LLM' and
                            player['player_llm_model'] and
                            player['final_score'] is not None):
                        player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])
                        if player_key in player_stats:
                            all_participants_for_rating.append((player_key, player['final_score'], 'player'))

                # 只处理有多个参与者的游戏
                if len(all_participants_for_rating) < 2:
                    continue

                # 准备ELO计算数据
                elo_players = []
                trueskill_players = []

                for participant_key, score, _ in all_participants_for_rating:
                    elo_players.append((participant_key, player_stats[participant_key].elo_rating, score))
                    trueskill_players.append((participant_key, player_stats[participant_key].trueskill_rating, score))

                # 更新ELO评分
                if len(elo_players) >= 2:
                    new_elo_ratings = self.elo_calculator.update_ratings_multiplayer(elo_players)
                    for participant_key, new_rating in new_elo_ratings.items():
                        old_rating = player_stats[participant_key].elo_rating
                        player_stats[participant_key].elo_rating = new_rating
                        logger.debug(f"ELO更新 {participant_key}: {old_rating:.1f} -> {new_rating:.1f}")

                # 更新TrueSkill评分
                if len(trueskill_players) >= 2:
                    new_trueskill_ratings = self.trueskill_calculator.update_ratings_multiplayer(trueskill_players)
                    for participant_key, new_rating in new_trueskill_ratings.items():
                        old_rating = player_stats[participant_key].trueskill_rating
                        player_stats[participant_key].trueskill_rating = new_rating
                        logger.debug(
                            f"TrueSkill更新 {participant_key}: μ {old_rating.mu:.2f}->{new_rating.mu:.2f}, σ {old_rating.sigma:.2f}->{new_rating.sigma:.2f}")

                games_processed += 1

            except Exception as e:
                logger.error(f"计算评分失败: {e}", exc_info=True)
                continue

        logger.info(f"评分计算完成，处理了 {games_processed} 个游戏")

    def _get_player_key(self, model_name: str, model_params: Optional[Dict[str, Any]]) -> str:
        """生成玩家唯一标识 - 改进版本，确保一致性"""
        if not model_name:
            return "unknown_model"

        # 标准化模型名称
        normalized_model_name = model_name.strip()

        if model_params:
            # 只包含非默认参数，并确保参数顺序一致
            filtered_params = {}
            for k, v in model_params.items():
                if v is not None and v != "":
                    # 标准化参数值
                    if isinstance(v, float):
                        # 对浮点数进行四舍五入以避免精度问题
                        filtered_params[k] = round(v, 6)
                    else:
                        filtered_params[k] = v

            if filtered_params:
                # 确保参数按键名排序，生成一致的字符串
                params_str = json.dumps(filtered_params, sort_keys=True, separators=(',', ':'))
                player_key = f"{normalized_model_name}#{params_str}"
                logger.debug(f"生成玩家键: {player_key}")
                return player_key

        logger.debug(f"生成玩家键: {normalized_model_name}")
        return normalized_model_name

    def _format_leaderboard(self, player_stats: Dict[str, PlayerStats]) -> List[Dict[str, Any]]:
        """格式化排行榜数据 - 改进版本，确保去重"""
        # 使用字典来去重，以model_name和model_params的组合作为键
        unique_entries = {}

        for player_key, stats in player_stats.items():
            # 创建唯一标识符
            unique_key = self._get_player_key(stats.model_name, stats.model_params)

            if unique_key in unique_entries:
                # 如果已存在，合并统计数据
                logger.warning(f"发现重复条目: {unique_key}，正在合并数据")
                existing = unique_entries[unique_key]

                # 合并统计数据
                existing['games_as_player'] += stats.games_as_player
                existing['games_as_designer'] += stats.games_as_designer
                existing['total_games'] += stats.total_games
                existing['overall_wins'] += stats.overall_wins
                existing['wins_as_player'] += stats.wins_as_player
                existing['wins_as_designer'] += stats.wins_as_designer
                existing['inter_designer_wins'] += stats.inter_designer_wins  # Merge inter-designer wins
                existing['total_cost'] += stats.total_cost
                existing['total_input_tokens'] += stats.total_input_tokens
                existing['total_output_tokens'] += stats.total_output_tokens

                existing['total_in_game_designer_score'] += stats.total_in_game_designer_score
                existing['total_meta_designer_score'] += stats.total_meta_designer_score

                # 重新计算平均值和比率
                existing['avg_score_as_player'] = round(existing['total_score_as_player'] / existing['games_as_player'],
                                                        2) if existing['games_as_player'] else 0.0
                existing['avg_score_as_designer'] = round(
                    existing['total_score_as_designer'] / existing['games_as_designer'], 2) if existing[
                    'games_as_designer'] else 0.0

                existing['avg_in_game_designer_score'] = round(
                    existing['total_in_game_designer_score'] / existing['games_as_designer'], 2) if existing[
                    'games_as_designer'] else 0.0
                existing['avg_meta_designer_score'] = round(
                    existing['total_meta_designer_score'] / existing['games_as_designer'], 2) if existing[
                    'games_as_designer'] else 0.0

                existing['win_rate_as_player'] = round((existing['wins_as_player'] / existing['games_as_player'] * 100),
                                                       1) if existing['games_as_player'] else 0.0
                existing['win_rate_as_designer'] = round(
                    (existing['wins_as_designer'] / existing['games_as_designer'] * 100), 1) if existing[
                    'games_as_designer'] else 0.0
                existing['inter_designer_win_rate'] = round(
                    (existing['inter_designer_wins'] / existing['games_as_designer'] * 100), 1) if existing[
                    'games_as_designer'] else 0.0  # Calculate inter-designer win rate
                existing['overall_win_rate'] = round((existing['overall_wins'] / existing['total_games'] * 100), 1) if \
                existing['total_games'] else 0.0
                existing['cost_per_game'] = round(existing['total_cost'] / existing['total_games'], 4) if existing[
                    'total_games'] else 0.0

                # 使用更高的ELO评分
                if stats.elo_rating > existing['elo_rating']:
                    existing['elo_rating'] = round(stats.elo_rating, 1)
                    existing['trueskill_rating'] = round(stats.conservative_trueskill_rating, 1)
                    existing['trueskill_mu'] = round(stats.trueskill_rating.mu, 2)
                    existing['trueskill_sigma'] = round(stats.trueskill_rating.sigma, 2)
            else:
                # 创建新条目
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
                    'avg_in_game_designer_score': round(stats.avg_in_game_designer_score, 2),
                    'avg_meta_designer_score': round(stats.avg_meta_designer_score, 2),
                    'win_rate_as_player': round(stats.win_rate_as_player, 1),
                    'win_rate_as_designer': round(stats.win_rate_as_designer, 1),
                    'inter_designer_win_rate': round(stats.inter_designer_win_rate, 1),
                    'overall_win_rate': round(stats.overall_win_rate, 1),
                    'overall_wins': stats.overall_wins,
                    'wins_as_player': stats.wins_as_player,
                    'wins_as_designer': stats.wins_as_designer,
                    'inter_designer_wins': stats.inter_designer_wins,
                    'cost_per_game': round(stats.cost_per_game, 4),
                    'total_cost': round(stats.total_cost, 4),
                    'total_input_tokens': stats.total_input_tokens,
                    'total_output_tokens': stats.total_output_tokens,
                    'total_in_game_designer_score': stats.total_in_game_designer_score,
                    'total_meta_designer_score': stats.total_meta_designer_score
                }
                unique_entries[unique_key] = entry

        # 转换为列表并按ELO评分排序
        leaderboard = list(unique_entries.values())
        leaderboard.sort(key=lambda x: x['elo_rating'], reverse=True)

        logger.info(f"格式化完成，排行榜条目数: {len(leaderboard)} (去重后)")
        for i, entry in enumerate(leaderboard[:5]):  # 打印前5名
            logger.info(
                f"  {i + 1}. {entry['model_name']}: ELO {entry['elo_rating']}, TrueSkill {entry['trueskill_rating']}, 玩家胜率 {entry['win_rate_as_player']}%, 设计师轮内胜率 {entry['inter_designer_win_rate']}%, 总体胜率 {entry['overall_win_rate']}%, 成本/局 ${entry['cost_per_game']:.4f}")

        return leaderboard

    async def _save_leaderboard_to_db(self, leaderboard_data: List[Dict[str, Any]], test_set_id: Optional[str] = None):
        """保存排行榜数据到数据库 - 使用单独的列而不是JSON，确保唯一性"""
        try:
            if not leaderboard_data:
                logger.warning("没有排行榜数据可保存")
                return

            async with self.db_pool.acquire() as conn:
                # 开始事务
                async with conn.transaction():
                    # 首先删除已有的同一测试集的排行榜数据
                    if test_set_id:
                        deleted_count = await conn.fetchval("SELECT COUNT(*) FROM leaderboards WHERE test_set_id = $1",
                                                            test_set_id)
                        await conn.execute("DELETE FROM leaderboards WHERE test_set_id = $1", test_set_id)
                    else:
                        deleted_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM leaderboards WHERE test_set_id IS NULL")
                        await conn.execute("DELETE FROM leaderboards WHERE test_set_id IS NULL")

                    logger.info(f"删除了 {deleted_count} 条旧的排行榜记录")

                    # 插入新的排行榜数据
                    inserted_count = 0
                    for rank, entry in enumerate(leaderboard_data, 1):
                        leaderboard_id = str(uuid.uuid4())

                        # 序列化model_params为JSON
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
                                                          avg_in_game_designer_score, avg_meta_designer_score,
                                                          win_rate_as_player, win_rate_as_designer,
                                                          inter_designer_win_rate, overall_win_rate,
                                                          wins_as_player, wins_as_designer, inter_designer_wins,
                                                          overall_wins,
                                                          cost_per_game, total_cost,
                                                          total_input_tokens, total_output_tokens, created_at)
                                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                                        $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29)
                                """,
                                leaderboard_id, test_set_id, rank, entry['model_name'], model_params_json,
                                entry['elo_rating'], entry['trueskill_rating'], entry['trueskill_mu'],
                                entry['trueskill_sigma'],
                                entry['games_as_player'], entry['games_as_designer'], entry['total_games'],
                                entry['avg_score_as_player'], entry['avg_score_as_designer'],
                                entry['avg_in_game_designer_score'], entry['avg_meta_designer_score'],
                                entry['win_rate_as_player'], entry['win_rate_as_designer'],
                                entry['inter_designer_win_rate'],
                                entry['overall_win_rate'], entry['wins_as_player'], entry['wins_as_designer'],
                                entry['inter_designer_wins'],
                                entry['overall_wins'],
                                entry['cost_per_game'], entry['total_cost'],
                                entry['total_input_tokens'], entry['total_output_tokens'], datetime.now()
                            )
                            inserted_count += 1
                        except Exception as e:
                            logger.error(f"插入排行榜条目失败 {entry['model_name']}: {e}")
                            continue

                    logger.info(f"成功保存了 {inserted_count} 条新的排行榜记录，测试集ID: {test_set_id or '全局'}")

        except Exception as e:
            logger.error(f"保存排行榜数据失败: {e}", exc_info=True)

    async def get_cached_leaderboard(self, test_set_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """从数据库获取缓存的排行榜数据 - 从单独的列中读取"""
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
                    seen_models = set()  # 用于检测重复

                    for row in rows:
                        logger.info(
                            f"[DEBUG] 数据库行: model={row['model_name']}, wins_as_player={row['wins_as_player']}, wins_as_designer={row['wins_as_designer']}, games_as_player={row['games_as_player']}, games_as_designer={row['games_as_designer']}, inter_designer_wins={row['inter_designer_wins']}")  # Log inter_designer_wins

                        # 解析model_params
                        model_params = None
                        if row['model_params']:
                            try:
                                model_params = json.loads(row['model_params'])
                            except Exception as e:
                                logger.warning(f"解析model_params失败: {e}")

                        # 创建唯一标识符
                        unique_key = self._get_player_key(row['model_name'], model_params)

                        if unique_key in seen_models:
                            logger.warning(f"跳过重复的排行榜条目: {unique_key}")
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
                            'avg_in_game_designer_score': float(row.get('avg_in_game_designer_score', 0.0)),
                            'avg_meta_designer_score': float(row.get('avg_meta_designer_score', 0.0)),
                            'win_rate_as_player': float(row['win_rate_as_player']),
                            'win_rate_as_designer': float(row['win_rate_as_designer']),
                            'inter_designer_win_rate': float(row['inter_designer_win_rate']),
                            # Load inter_designer_win_rate
                            'overall_win_rate': float(row['overall_win_rate']),
                            'wins_as_player': row['wins_as_player'],
                            'wins_as_designer': row['wins_as_designer'],
                            'inter_designer_wins': row['inter_designer_wins'],  # Load inter_designer_wins
                            'overall_wins': row['overall_wins'],
                            'cost_per_game': float(row['cost_per_game']),
                            'total_cost': float(row['total_cost']),
                            'total_input_tokens': row['total_input_tokens'],
                            'total_output_tokens': row['total_output_tokens']
                        }

                        logger.info(
                            f"[DEBUG] 返回条目: model={entry['model_name']}, wins_as_player={entry['wins_as_player']}, wins_as_designer={entry['wins_as_designer']}, inter_designer_wins={entry['inter_designer_wins']}")  # Log inter_designer_wins

                        leaderboard_data.append(entry)

                    created_at = rows[0]['created_at'] if rows else datetime.now()
                    logger.info(f"获取到缓存的排行榜数据，创建于 {created_at}，共 {len(leaderboard_data)} 条记录")
                    return leaderboard_data

                logger.info(f"没有找到缓存的排行榜数据，测试集ID: {test_set_id or '全局'}")
                return None

        except Exception as e:
            logger.error(f"获取缓存排行榜数据失败: {e}", exc_info=True)
            return None

    async def _fetch_designer_wins_from_analytics(self) -> Dict[str, Dict[str, Any]]:
        """从game_analytics表获取设计师胜利数据"""
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

                logger.info(f"从game_analytics获取到 {len(result)} 条设计师排名数据")
                return result

        except Exception as e:
            logger.error(f"获取设计师胜利数据失败: {e}", exc_info=True)
            return {}


async def create_leaderboard_tables(db_pool):
    """创建排行榜相关的数据库表"""
    try:
        async with db_pool.acquire() as conn:
            # 添加测试集ID到games表
            try:
                await conn.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS test_set_id TEXT")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_games_test_set_id ON games(test_set_id)")
            except Exception as e:
                logger.info(f"Games表已存在test_set_id列: {e}")

            # 添加token使用情况到game_players表
            try:
                await conn.execute("ALTER TABLE game_players ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0")
                await conn.execute("ALTER TABLE game_players ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0")
            except Exception as e:
                logger.info(f"Game_players表已存在token列: {e}")

            # 创建测试集表
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
                                   completed_games INTEGER     DEFAULT 0
                               )
                               """)

            # 删除旧的排行榜表（如果存在）
            await conn.execute("DROP TABLE IF EXISTS leaderboards")

            # 创建新的排行榜表，使用单独的列，并添加唯一约束
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
                                   avg_in_game_designer_score DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   avg_meta_designer_score    DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
                                   win_rate_as_player         DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   win_rate_as_designer       DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   inter_designer_win_rate    DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   overall_win_rate           DECIMAL(5, 2)  NOT NULL DEFAULT 0.0,
                                   wins_as_player             INTEGER        NOT NULL DEFAULT 0,
                                   wins_as_designer           INTEGER        NOT NULL DEFAULT 0,
                                   inter_designer_wins        INTEGER        NOT NULL DEFAULT 0,
                                   overall_wins               INTEGER        NOT NULL DEFAULT 0,
                                   cost_per_game              DECIMAL(10, 6) NOT NULL DEFAULT 0.0,
                                   total_cost                 DECIMAL(10, 6) NOT NULL DEFAULT 0.0,
                                   total_input_tokens         INTEGER        NOT NULL DEFAULT 0,
                                   total_output_tokens        INTEGER        NOT NULL DEFAULT 0,
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

            logger.info("排行榜表创建成功")

    except Exception as e:
        logger.error(f"创建排行榜表失败: {e}")
