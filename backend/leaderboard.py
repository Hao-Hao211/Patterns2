import math
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict
import asyncpg
import trueskill

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
    overall_wins: int = 0  # 总体胜利次数（在单局游戏中得分最高）
    total_games: int = 0  # 总游戏次数（包括作为玩家和设计师）
    elo_rating: float = 1500.0
    trueskill_rating: Optional[trueskill.Rating] = None

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
    def win_rate_as_player(self) -> float:
        return (self.wins_as_player / self.games_as_player * 100) if self.games_as_player > 0 else 0.0

    @property
    def win_rate_as_designer(self) -> float:
        return (self.wins_as_designer / self.games_as_designer * 100) if self.games_as_designer > 0 else 0.0

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

            logger.info(f"成功计算排行榜，共 {len(leaderboard)} 个条目")
            return leaderboard

        except Exception as e:
            logger.error(f"计算排行榜失败: {e}", exc_info=True)
            return []

    async def _fetch_games_data(self, test_set_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取游戏数据"""
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
                            SELECT g.id as game_id, \
                                   g.designer_type, \
                                   g.designer_llm_model, \
                                   g.designer_llm_model_params, \
                                   g.created_at, \
                                   gp.player_name_in_game, \
                                   gp.player_type, \
                                   gp.player_llm_model, \
                                   gp.player_llm_model_params, \
                                   gp.final_score
                            FROM games g
                                     JOIN game_players gp ON g.id = gp.game_id
                            WHERE g.test_set_id = $1 \
                              AND gp.player_type = 'LLM'
                            ORDER BY g.created_at, g.id \
                            """
                    rows = await conn.fetch(query, test_set_id)
                    logger.info(f"测试集 {test_set_id} 查询到 {len(rows)} 条记录")
                else:
                    # 获取所有游戏
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
                                   gp.final_score
                            FROM games g
                                     JOIN game_players gp ON g.id = gp.game_id
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
                    logger.info(f"游戏 {i + 1}: {len(game['players'])} 个玩家, 设计师: {game['designer_type']}")
                    for j, player in enumerate(game['players']):
                        logger.info(f"  玩家 {j + 1}: {player['player_llm_model']}, 分数: {player['final_score']}")

                return games_data

        except Exception as e:
            logger.error(f"获取游戏数据失败: {e}", exc_info=True)
            return []

    async def _collect_basic_stats(self, games_data: List[Dict[str, Any]], player_stats: Dict[str, PlayerStats]):
        """收集基础统计数据 - 修正版本"""
        logger.info("开始收集基础统计数据")

        for game in games_data:
            try:
                # 收集该局游戏中所有参与者的得分
                all_participants = []

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
                        player_stats[designer_key].total_games += 1

                        all_participants.append((designer_key, designer_score, 'designer'))

                        logger.debug(f"游戏 {game['id']}: 设计师 {designer_key} 得分 {designer_score}")

                # 处理玩家
                valid_players = []
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

                        valid_players.append((player_key, player['final_score']))
                        all_participants.append((player_key, player['final_score'], 'player'))

                        logger.debug(f"游戏 {game['id']}: 玩家 {player_key} 得分 {player['final_score']}")

                # 计算玩家胜率（在该局游戏的玩家中得分最高）
                if valid_players:
                    max_player_score = max(score for _, score in valid_players)
                    for player_key, score in valid_players:
                        if score == max_player_score:
                            player_stats[player_key].wins_as_player += 1
                            logger.debug(f"游戏 {game['id']}: 玩家获胜 {player_key} 得分 {score}")

                # 计算总体胜率（在该局游戏的所有参与者中得分最高）
                if all_participants:
                    max_overall_score = max(score for _, score, _ in all_participants)
                    winners = [participant_key for participant_key, score, _ in all_participants if
                               score == max_overall_score]

                    logger.debug(f"游戏 {game['id']}: 最高分 {max_overall_score}, 获胜者 {winners}")

                    for participant_key in winners:
                        player_stats[participant_key].overall_wins += 1
                        logger.debug(f"游戏 {game['id']}: 总体获胜 {participant_key} 得分 {max_overall_score}")

            except Exception as e:
                logger.error(f"处理游戏 {game.get('id', 'unknown')} 数据失败: {e}", exc_info=True)
                continue

        # 计算设计师胜率需要按轮次分组
        games_by_round = self._group_games_by_round(games_data)

        for round_games in games_by_round:
            try:
                # 收集这一轮中所有设计师的得分
                round_designers = []

                for game in round_games:
                    if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                        designer_key = self._get_player_key(game['designer_llm_model'],
                                                            game['designer_llm_model_params'])

                        # 计算设计师得分
                        player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                        if len(player_scores) >= 2:
                            max_score = max(player_scores)
                            min_score = min(player_scores)
                            designer_score = 2 * (max_score - min_score)
                            round_designers.append((designer_key, designer_score))

                # 计算设计师胜率（在同一轮中比较所有设计师）
                if round_designers:
                    max_designer_score = max(score for _, score in round_designers)
                    for designer_key, score in round_designers:
                        if score == max_designer_score:
                            player_stats[designer_key].wins_as_designer += 1
                            logger.debug(f"设计师获胜 {designer_key}: 得分 {score} (轮内最高)")

            except Exception as e:
                logger.error(f"处理轮换设计师胜率失败: {e}", exc_info=True)
                continue

        # 打印统计结果
        logger.info(f"统计完成，共 {len(player_stats)} 个模型:")
        for key, stats in player_stats.items():
            logger.info(
                f"  {key}: 玩家游戏{stats.games_as_player}局(胜{stats.wins_as_player}), 设计师游戏{stats.games_as_designer}局(胜{stats.wins_as_designer}), 总体胜{stats.overall_wins}/{stats.total_games}")

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
                all_participants = []

                # 添加设计师
                if game['designer_type'] == 'LLM' and game['designer_llm_model']:
                    designer_key = self._get_player_key(game['designer_llm_model'], game['designer_llm_model_params'])
                    if designer_key in player_stats:
                        # 计算设计师得分
                        player_scores = [p['final_score'] for p in game['players'] if p['final_score'] is not None]
                        if len(player_scores) >= 2:
                            max_score = max(player_scores)
                            min_score = min(player_scores)
                            designer_score = 2 * (max_score - min_score)
                            all_participants.append((designer_key, designer_score))

                # 添加玩家
                for player in game['players']:
                    if (player['player_type'] == 'LLM' and
                            player['player_llm_model'] and
                            player['final_score'] is not None):
                        player_key = self._get_player_key(player['player_llm_model'], player['player_llm_model_params'])
                        if player_key in player_stats:
                            all_participants.append((player_key, player['final_score']))

                # 只处理有多个参与者的游戏
                if len(all_participants) < 2:
                    continue

                # 准备ELO计算数据
                elo_players = []
                trueskill_players = []

                for participant_key, score in all_participants:
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
                'overall_wins': stats.overall_wins
            }
            leaderboard.append(entry)

        # 按ELO评分排序
        leaderboard.sort(key=lambda x: x['elo_rating'], reverse=True)

        logger.info(f"格式化完成，排行榜条目数: {len(leaderboard)}")
        for i, entry in enumerate(leaderboard[:5]):  # 打印前5名
            logger.info(
                f"  {i + 1}. {entry['model_name']}: ELO {entry['elo_rating']}, TrueSkill {entry['trueskill_rating']}, 总体胜率 {entry['overall_win_rate']}%, 总体胜利 {entry['overall_wins']}/{entry['total_games']}")

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
                                   created_at
                                   TIMESTAMPTZ
                                   DEFAULT
                                   NOW
                               (
                               ),
                                   status TEXT DEFAULT 'created',
                                   total_games INTEGER DEFAULT 0,
                                   completed_games INTEGER DEFAULT 0
                                   )
                               """)

            # 创建排行榜缓存表
            await conn.execute("""
                               CREATE TABLE IF NOT EXISTS leaderboards
                               (
                                   id
                                   TEXT
                                   PRIMARY
                                   KEY,
                                   test_set_id
                                   TEXT,
                                   data
                                   JSONB
                                   NOT
                                   NULL,
                                   created_at
                                   TIMESTAMPTZ
                                   DEFAULT
                                   NOW
                               (
                               ),
                                   FOREIGN KEY
                               (
                                   test_set_id
                               ) REFERENCES test_sets
                               (
                                   id
                               ) ON DELETE CASCADE
                                   )
                               """)

            await conn.execute("CREATE INDEX IF NOT EXISTS idx_leaderboards_test_set_id ON leaderboards(test_set_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_test_sets_created_at ON test_sets(created_at DESC)")

            logger.info("排行榜表创建成功")

    except Exception as e:
        logger.error(f"创建排行榜表失败: {e}")
