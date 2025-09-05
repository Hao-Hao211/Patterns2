import math
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict
import asyncpg

logger = logging.getLogger(__name__)

@dataclass
class PlayerStats:
    """玩家统计数据"""
    model_name: str
    model_params: Optional[Dict[str, Any]] = None
    games_as_player: int = 0
    games_as_designer: int = 0
    total_score_as_player: int = 0
    total_score_as_designer: int = 0
    wins_as_player: int = 0
    wins_as_designer: int = 0
    elo_rating: float = 1500.0
    trueskill_mu: float = 25.0
    trueskill_sigma: float = 8.333

    @property
    def avg_score_as_player(self) -> float:
        return self.total_score_as_player / self.games_as_player if self.games_as_player > 0 else 0.0

    @property
    def avg_score_as_designer(self) -> float:
        return self.total_score_as_designer / self.games_as_designer if self.games_as_designer > 0 else 0.0

    @property
    def win_rate_as_player(self) -> float:
        return (self.wins_as_player / self.games_as_player * 100) if self.games_as_player > 0 else 0.0

    @property
    def win_rate_as_designer(self) -> float:
        return (self.wins_as_designer / self.games_as_designer * 100) if self.games_as_designer > 0 else 0.0

class ELOCalculator:
    """ELO评分计算器"""

    @staticmethod
    def expected_score(rating_a: float, rating_b: float) -> float:
        """计算期望得分"""
        return 1.0 / (1.0 + 10**((rating_b - rating_a) / 400))

    @staticmethod
    def update_rating(rating: float, expected: float, actual: float, k_factor: float = 32) -> float:
        """更新ELO评分"""
        return rating + k_factor * (actual - expected)

    @classmethod
    def update_ratings_multiplayer(cls, players: List[Tuple[str, float, int]], k_factor: float = 32) -> Dict[str, float]:
        """
        多人游戏ELO评分更新
        players: [(player_id, current_rating, final_score), ...]
        返回: {player_id: new_rating, ...}
        """
        if len(players) < 2:
            return {player_id: rating for player_id, rating, _ in players}

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
    """TrueSkill评分计算器（简化版）"""

    BETA = 4.166  # 技能差异参数
    TAU = 0.083   # 动态因子
    DRAW_PROBABILITY = 0.1  # 平局概率

    @staticmethod
    def gaussian_cdf(x: float) -> float:
        """高斯累积分布函数近似"""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def gaussian_pdf(x: float) -> float:
        """高斯概率密度函数"""
        return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

    @classmethod
    def v_function(cls, t: float, epsilon: float) -> float:
        """V函数"""
        t_epsilon = t - epsilon
        denom = cls.gaussian_cdf(t_epsilon)
        if denom < 1e-10:
            return -t_epsilon
        return cls.gaussian_pdf(t_epsilon) / denom

    @classmethod
    def w_function(cls, t: float, epsilon: float) -> float:
        """W函数"""
        t_epsilon = t - epsilon
        denom = cls.gaussian_cdf(t_epsilon)
        if denom < 1e-10:
            return 1.0
        v = cls.v_function(t, epsilon)
        return v * (v + t_epsilon)

    @classmethod
    def update_rating(cls, mu: float, sigma: float, opponent_mu: float, opponent_sigma: float,
                     outcome: float) -> Tuple[float, float]:
        """
        更新TrueSkill评分
        outcome: 1.0=胜利, 0.5=平局, 0.0=失败
        """
        # 计算组合方差
        c = math.sqrt(sigma**2 + opponent_sigma**2 + 2 * cls.BETA**2)

        # 计算t值
        t = (mu - opponent_mu) / c

        # 根据结果计算epsilon
        if outcome == 1.0:  # 胜利
            epsilon = cls.DRAW_PROBABILITY / c
            v = cls.v_function(t, epsilon)
            w = cls.w_function(t, epsilon)
        elif outcome == 0.0:  # 失败
            epsilon = cls.DRAW_PROBABILITY / c
            v = -cls.v_function(-t, epsilon)
            w = cls.w_function(-t, epsilon)
        else:  # 平局
            epsilon = cls.DRAW_PROBABILITY / c
            v = 0.0
            w = 1.0 - cls.DRAW_PROBABILITY

        # 更新mu和sigma
        mu_multiplier = sigma**2 / c
        new_mu = mu + mu_multiplier * v

        sigma_multiplier = sigma**2 / (c**2)
        new_sigma = sigma * math.sqrt(max(1 - sigma_multiplier * w, 0.01))

        # 添加动态因子
        new_sigma = math.sqrt(new_sigma**2 + cls.TAU**2)

        return new_mu, new_sigma

    @classmethod
    def update_ratings_multiplayer(cls, players: List[Tuple[str, float, float, int]]) -> Dict[str, Tuple[float, float]]:
        """
        多人游戏TrueSkill评分更新
        players: [(player_id, mu, sigma, final_score), ...]
        返回: {player_id: (new_mu, new_sigma), ...}
        """
        if len(players) < 2:
            return {player_id: (mu, sigma) for player_id, mu, sigma, _ in players}

        # 按分数排序
        sorted_players = sorted(players, key=lambda x: x[3], reverse=True)
        new_ratings = {}

        for i, (player_id, mu, sigma, score) in enumerate(sorted_players):
            total_mu_change = 0.0
            total_sigma_change = 0.0
            comparisons = 0

            # 与其他所有玩家比较
            for j, (other_id, other_mu, other_sigma, other_score) in enumerate(sorted_players):
                if i != j:
                    # 确定结果
                    if score > other_score:
                        outcome = 1.0
                    elif score < other_score:
                        outcome = 0.0
                    else:
                        outcome = 0.5

                    # 更新评分
                    new_mu, new_sigma = cls.update_rating(mu, sigma, other_mu, other_sigma, outcome)
                    total_mu_change += (new_mu - mu)
                    total_sigma_change += (new_sigma - sigma)
                    comparisons += 1

            # 平均变化
            if comparisons > 0:
                final_mu = mu + total_mu_change / comparisons
                final_sigma = sigma + total_sigma_change / comparisons
            else:
                final_mu, final_sigma = mu, sigma

            # 确保sigma不会太小
            final_sigma = max(final_sigma, 1.0)
            new_ratings[player_id] = (final_mu, final_sigma)

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

            logger.info(f"成功计算排行榜，共 {len(leaderboard)} 个条目")
            return leaderboard

        except Exception as e:
            logger.error(f"计算排行榜失败: {e}", exc_info=True)
            return []

    async def _fetch_games_data(self, test_set_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取游戏数据 - 修复版本"""
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

                if test_set_id:
                    # 获取特定测试集的游戏
                    query = """
                        SELECT 
                            g.id as game_id,
                            g.designer_type,
                            g.designer_llm_model,
                            g.designer_llm_model_params,
                            g.created_at,
                            gp.player_name_in_game,
                            gp.player_type,
                            gp.player_llm_model,
                            gp.player_llm_model_params,
                            gp.final_score
                        FROM games g
                        JOIN game_players gp ON g.id = gp.game_id
                        WHERE g.test_set_id = $1 AND gp.player_type = 'LLM'
                        ORDER BY g.created_at, g.id
                    """
                    rows = await conn.fetch(query, test_set_id)
                    logger.info(f"测试集 {test_set_id} 查询到 {len(rows)} 条记录")
                else:
                    # 获取所有游戏
                    query = """
                        SELECT 
                            g.id as game_id,
                            g.designer_type,
                            g.designer_llm_model,
                            g.designer_llm_model_params,
                            g.created_at,
                            gp.player_name_in_game,
                            gp.player_type,
                            gp.player_llm_model,
                            gp.player_llm_model_params,
                            gp.final_score
                        FROM games g
                        JOIN game_players gp ON g.id = gp.game_id
                        WHERE gp.player_type = 'LLM'
                        ORDER BY g.created_at, g.id
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

                    player_data = {
                        'player_name_in_game': row['player_name_in_game'],
                        'player_type': row['player_type'],
                        'player_llm_model': row['player_llm_model'],
                        'player_llm_model_params': player_params,
                        'final_score': row['final_score'] if row['final_score'] is not None else 0
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
                            'players': []
                        }

                    games_by_id[game_id]['players'].append(player_data)

                # 转换为列表
                games_data = list(games_by_id.values())

                logger.info(f"成功处理 {len(games_data)} 个游戏")

                # 打印一些调试信息
                for i, game in enumerate(games_data[:3]):  # 只打印前3个游戏的信息
                    logger.info(f"游戏 {i+1}: {len(game['players'])} 个玩家, 设计师: {game['designer_type']}")
                    for j, player in enumerate(game['players']):
                        logger.info(f"  玩家 {j+1}: {player['player_llm_model']}, 分数: {player['final_score']}")

                return games_data

        except Exception as e:
            logger.error(f"获取游戏数据失败: {e}", exc_info=True)
            return []

    async def _collect_basic_stats(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """收集基础统计数据 - 修复版本"""
        logger.info("开始收集基础统计数据")

        for game in games_data:
            try:
                # 处理设计师
                if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                    designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])
                    if designer_key not in player_stats:
                        player_stats[designer_key] = PlayerStats(
                            model_name=game['designer_llm_model'],
                            model_params=game['designer_llm_model_params']
                        )

                    # 计算设计师得分（玩家得分差的2倍）
                    player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                    if len(player_scores) >= 2:
                        max_score = max(player_scores)
                        min_score = min(player_scores)
                        designer_score = 2 * (max_score - min_score)

                        player_stats[designer_key].games_as_designer += 1
                        player_stats[designer_key].total_score_as_designer += designer_score

                        # 设计师获胜条件：玩家得分差异大（说明模式有挑战性）
                        if designer_score > 0:
                            player_stats[designer_key].wins_as_designer += 1

                        logger.debug(f"设计师 {designer_key}: 得分差 {max_score}-{min_score}={designer_score}")

                # 处理玩家
                if len(game['players']) >= 1:
                    # 找出最高分
                    valid_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                    if valid_scores:
                        max_score = max(valid_scores)

                        for player in game['players']:
                            if player['player_type'] == 'LLM' and player['player_llm_model']:
                                player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])

                                if player_key not in player_stats:
                                    player_stats[player_key] = PlayerStats(
                                        model_name=player['player_llm_model'],
                                        model_params=player['player_llm_model_params']
                                    )

                                player_stats[player_key].games_as_player += 1

                                if player['final_score'] is not None:
                                    player_stats[player_key].total_score_as_player += player['final_score']

                                    # 玩家获胜条件：得分最高
                                    if player['final_score'] == max_score:
                                        player_stats[player_key].wins_as_player += 1

                                    logger.debug(f"玩家 {player_key}: 得分 {player['final_score']}, 最高分 {max_score}")

            except Exception as e:
                logger.error(f"处理游戏数据失败: {e}", exc_info=True)
                continue

        # 打印统计结果
        logger.info(f"统计完成，共 {len(player_stats)} 个模型:")
        for key, stats in player_stats.items():
            logger.info(f"  {key}: 玩家游戏{stats.games_as_player}局(胜{stats.wins_as_player}), 设计师游戏{stats.games_as_designer}局(胜{stats.wins_as_designer})")

    async def _calculate_ratings(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """计算ELO和TrueSkill评分 - 修复版本"""
        logger.info("开始计算评分")

        # 按时间顺序处理游戏
        games_processed = 0
        for game in games_data:
            try:
                # 只处理有多个LLM玩家的游戏
                llm_players = [p for p in game['players'] if p['player_type'] == 'LLM' and p['player_llm_model']]
                if len(llm_players) < 2:
                    continue

                # 准备ELO计算数据
                elo_players = []
                trueskill_players = []

                for player in llm_players:
                    player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])
                    if player_key in player_stats and player['final_score'] is not None:
                        elo_players.append((player_key, player_stats[player_key].elo_rating, player['final_score']))
                        trueskill_players.append((
                            player_key,
                            player_stats[player_key].trueskill_mu,
                            player_stats[player_key].trueskill_sigma,
                            player['final_score']
                        ))

                # 更新ELO评分
                if len(elo_players) >= 2:
                    new_elo_ratings = self.elo_calculator.update_ratings_multiplayer(elo_players)
                    for player_key, new_rating in new_elo_ratings.items():
                        old_rating = player_stats[player_key].elo_rating
                        player_stats[player_key].elo_rating = new_rating
                        logger.debug(f"ELO更新 {player_key}: {old_rating:.1f} -> {new_rating:.1f}")

                # 更新TrueSkill评分
                if len(trueskill_players) >= 2:
                    new_trueskill_ratings = self.trueskill_calculator.update_ratings_multiplayer(trueskill_players)
                    for player_key, (new_mu, new_sigma) in new_trueskill_ratings.items():
                        old_mu = player_stats[player_key].trueskill_mu
                        old_sigma = player_stats[player_key].trueskill_sigma
                        player_stats[player_key].trueskill_mu = new_mu
                        player_stats[player_key].trueskill_sigma = new_sigma
                        logger.debug(f"TrueSkill更新 {player_key}: μ {old_mu:.2f}->{new_mu:.2f}, σ {old_sigma:.2f}->{new_sigma:.2f}")

                games_processed += 1

            except Exception as e:
                logger.error(f"计算评分失败: {e}", exc_info=True)
                continue

        logger.info(f"评分计算完成，处理了 {games_processed} 个游戏")

    def _get_player_key(self, model_name: str, model_params: Optional[Dict[str, Any]]) -> str:
        """生成玩家唯一标识"""
        if model_params:
            # 只包含非默认参数
            filtered_params = {k: v for k, v in model_params.items() if v is not None}
            if filtered_params:
                params_str = json.dumps(filtered_params, sort_keys=True)
                return f"{model_name}#{params_str}"
        return model_name

    def _format_leaderboard(self, player_stats: Dict[str, PlayerStats]) -> List[Dict[str, Any]]:
        """格式化排行榜数据"""
        leaderboard = []

        for player_key, stats in player_stats.items():
            # 计算TrueSkill保守评分
            trueskill_rating = stats.trueskill_mu - 3 * stats.trueskill_sigma

            entry = {
                'model_name': stats.model_name,
                'model_params': stats.model_params,
                'elo_rating': round(stats.elo_rating, 1),
                'trueskill_rating': round(trueskill_rating, 1),
                'trueskill_mu': round(stats.trueskill_mu, 2),
                'trueskill_sigma': round(stats.trueskill_sigma, 2),
                'games_as_player': stats.games_as_player,
                'games_as_designer': stats.games_as_designer,
                'avg_score_as_player': round(stats.avg_score_as_player, 2),
                'avg_score_as_designer': round(stats.avg_score_as_designer, 2),
                'win_rate_as_player': round(stats.win_rate_as_player, 1),
                'win_rate_as_designer': round(stats.win_rate_as_designer, 1),
                'total_games': stats.games_as_player + stats.games_as_designer
            }
            leaderboard.append(entry)

        # 按ELO评分排序
        leaderboard.sort(key=lambda x: x['elo_rating'], reverse=True)

        logger.info(f"格式化完成，排行榜条目数: {len(leaderboard)}")
        for i, entry in enumerate(leaderboard[:5]):  # 打印前5名
            logger.info(f"  {i+1}. {entry['model_name']}: ELO {entry['elo_rating']}, 玩家游戏 {entry['games_as_player']}")

        return leaderboard

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

            # 创建测试集表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS test_sets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    config JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    status TEXT DEFAULT 'created',
                    total_games INTEGER DEFAULT 0,
                    completed_games INTEGER DEFAULT 0
                )
            """)

            # 创建排行榜缓存表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS leaderboards (
                    id TEXT PRIMARY KEY,
                    test_set_id TEXT,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    FOREIGN KEY (test_set_id) REFERENCES test_sets(id) ON DELETE CASCADE
                )
            """)

            await conn.execute("CREATE INDEX IF NOT EXISTS idx_leaderboards_test_set_id ON leaderboards(test_set_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_created_at ON test_sets(created_at DESC)")

            logger.info("排行榜表创建成功")

    except Exception as e:
        logger.error(f"创建排行榜表失败: {e}")
