# import os
# import uuid
# import json
# import logging
# import asyncio
# from datetime import datetime
# from fastapi import FastAPI, HTTPException, Request
# from fastapi.responses import JSONResponse
# from fastapi.exceptions import RequestValidationError
# from fastapi.middleware.cors import CORSMiddleware
# from pydantic import BaseModel, Field, validator
# from typing import List, Optional, Literal, Dict, Any, Union
# import asyncpg
# from contextlib import asynccontextmanager
# import openai
# from dotenv import load_dotenv
#
# # 加载环境变量
# load_dotenv()
#
# # 配置日志
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)
#
# # 初始化OpenAI客户端
# openai_client = openai.OpenAI(
#     api_key=os.getenv("OPENAI_API_KEY")
# )
#
#
# # --- 全局异常处理器 ---
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     # 启动时创建数据库连接池
#     global db_pool
#     try:
#         db_pool = await asyncpg.create_pool(DATABASE_URL)
#         logger.info("数据库连接池创建成功")
#
#         # 自动创建表（如果不存在）
#         await create_tables_if_not_exist()
#
#     except Exception as e:
#         logger.error(f"数据库连接失败: {e}")
#         db_pool = None
#
#     yield
#
#     # 关闭时清理连接池
#     if db_pool:
#         await db_pool.close()
#         logger.info("数据库连接池已关闭")
#
#
# async def create_tables_if_not_exist():
#     """自动创建数据库表（如果不存在）"""
#     if not db_pool:
#         return
#
#     try:
#         async with db_pool.acquire() as conn:
#             # 检查表是否存在
#             result = await conn.fetchval(
#                 "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'games')"
#             )
#
#             if not result:
#                 logger.info("数据库表不存在，正在创建...")
#
#                 # 创建 games 表
#                 await conn.execute("""
#                                    CREATE TABLE games
#                                    (
#                                        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#                                        created_at            TIMESTAMPTZ      DEFAULT NOW(),
#                                        grid_size             INTEGER NOT NULL,
#                                        num_symbols           INTEGER NOT NULL,
#                                        designer_type         TEXT    NOT NULL,
#                                        designer_llm_model    TEXT,
#                                        designer_pattern_mode TEXT,
#                                        master_pattern        JSONB   NOT NULL,
#                                        game_config_dump      JSONB
#                                    )
#                                    """)
#
#                 # 创建 game_players 表
#                 await conn.execute("""
#                                    CREATE TABLE game_players
#                                    (
#                                        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#                                        game_id             UUID NOT NULL REFERENCES games (id) ON DELETE CASCADE,
#                                        player_name_in_game TEXT NOT NULL,
#                                        player_type         TEXT NOT NULL,
#                                        player_llm_model    TEXT,
#                                        final_score         INTEGER,
#                                        final_guess         JSONB,
#                                        action_log          JSONB,
#                                        queried_cells       JSONB
#                                    )
#                                    """)
#
#                 # 创建索引
#                 await conn.execute("CREATE INDEX idx_game_players_game_id ON game_players(game_id)")
#                 await conn.execute("CREATE INDEX idx_games_created_at ON games(created_at DESC)")
#
#                 logger.info("数据库表创建成功！")
#             else:
#                 logger.info("数据库表已存在")
#
#     except Exception as e:
#         logger.error(f"创建数据库表失败: {e}")
#
#
# # --- FastAPI应用初始化 ---
# app = FastAPI(title="Patterns II Game Backend", lifespan=lifespan)
#
#
# @app.exception_handler(RequestValidationError)
# async def validation_exception_handler(request: Request, exc: RequestValidationError):
#     """
#     捕获并格式化Pydantic验证错误，确保返回统一的JSON格式。
#     """
#     error_messages = []
#     for error in exc.errors():
#         field = " -> ".join(str(loc) for loc in error["loc"])
#         message = error["msg"]
#         error_messages.append(f"Field '{field}': {message}")
#
#     detail = "; ".join(error_messages)
#     logger.error(f"请求验证失败: {detail}")
#
#     return JSONResponse(
#         status_code=422,
#         content={"detail": detail},
#     )
#
#
# # --- 数据模型定义 ---
# Symbol = Literal["+", "○", "△", "□", "★", "✖"]
# Cell = Optional[Symbol]
# Grid = List[List[Cell]]
#
# ALL_SYMBOLS_PY: List[Symbol] = ["○", "△", "✖", "□", "★", "+"]
#
#
# class PositionModel(BaseModel):
#     row: int
#     col: int
#
#
# # 修复后的LLM Player API模型
# class LLMPlayerTurnRequest(BaseModel):
#     """LLM玩家单次行动请求"""
#     playerId: str
#     playerName: str
#     gridSize: int
#     symbolsInUse: List[Symbol]  # 移到 currentGrid 之前
#     currentGrid: List[List[str]]  # 移到 symbolsInUse 之后
#     llmModel: Optional[str] = "gpt-4o"
#     turnNumber: int = 1
#
#     @validator('currentGrid')
#     def validate_grid(cls, v, values):
#         """验证网格格式"""
#         grid_size = values.get('gridSize')
#         if grid_size is None:
#             raise ValueError("gridSize must be provided before currentGrid")
#
#         symbols_in_use = values.get('symbolsInUse', [])
#         if not symbols_in_use:
#             raise ValueError("symbolsInUse must be provided and non-empty before currentGrid")
#
#         if len(v) != grid_size:
#             raise ValueError(f"Grid must have {grid_size} rows, got {len(v)}")
#
#         for i, row in enumerate(v):
#             if not isinstance(row, list):
#                 raise ValueError(f"Row {i} must be a list, got {type(row)}")
#             if len(row) != grid_size:
#                 raise ValueError(f"Row {i} must have {grid_size} columns, got {len(row)}")
#
#             for j, cell in enumerate(row):
#                 if not isinstance(cell, str):
#                     raise ValueError(f"Cell at ({i},{j}) must be a string, got {type(cell)}: {cell}")
#                 if cell != "?" and cell not in symbols_in_use:
#                     raise ValueError(
#                         f"Invalid cell value at ({i},{j}): '{cell}'. Must be '?' or one of {symbols_in_use}")
#
#         return v
#
#
# class LLMPlayerTurnResponse(BaseModel):
#     """LLM玩家单次行动响应"""
#     action: Literal["observe", "guess", "final_guess", "give_up"]
#     cellsToObserve: Optional[List[PositionModel]] = None
#     guessGrid: Optional[Grid] = None
#     reasoning: str = ""
#     confidence: Optional[float] = None
#
#
# # API请求/响应模型
# class DesignPatternRequest(BaseModel):
#     gridSize: int = Field(..., ge=3, le=6)
#     numSymbols: int = Field(..., ge=2, le=len(ALL_SYMBOLS_PY))
#     llmModel: Optional[str] = "gpt-3.5-turbo"
#     prompt: Optional[str] = None
#
#
# class DesignPatternResponse(BaseModel):
#     pattern: Grid
#
#
# # 游戏保存相关模型 - 修复数据类型问题
# class PlayerStateInGame(BaseModel):
#     player_name_in_game: str
#     player_type: Literal["Human", "LLM"]
#     player_llm_model: Optional[str] = None
#     final_score: int
#     final_guess: Optional[List[List[str]]] = None  # 改为字符串类型，避免严格验证
#     action_log: Optional[List[str]] = None
#     queried_cells: Optional[List[PositionModel]] = None
#
#
# class GameCreateRequest(BaseModel):
#     grid_size: int
#     num_symbols: int
#     designer_type: Literal["Human", "LLM"]
#     designer_llm_model: Optional[str] = None
#     designer_pattern_mode: Optional[str] = None
#     master_pattern: List[List[str]]  # 改为字符串类型
#     game_config_dump: Dict[str, Any]
#     players: List[PlayerStateInGame]
#
#
# class GameCreateResponse(BaseModel):
#     message: str
#     game_id: str
#
#
# # 游戏历史相关模型
# class GameSummaryItem(BaseModel):
#     id: str
#     created_at: datetime
#     grid_size: int
#     num_symbols: int
#     designer_type: str
#
#
# class GamePlayerDetailResponse(BaseModel):
#     player_name_in_game: str
#     player_type: Literal["Human", "LLM"]
#     player_llm_model: Optional[str] = None
#     final_score: Optional[int] = None
#     final_guess: Optional[List[List[str]]] = None  # 改为字符串类型
#     action_log: Optional[List[str]] = None
#     queried_cells: Optional[List[PositionModel]] = None
#
#
# class GameDetailResponse(BaseModel):
#     id: str
#     created_at: datetime
#     grid_size: int
#     num_symbols: int
#     designer_type: Literal["Human", "LLM"]
#     designer_llm_model: Optional[str] = None
#     designer_pattern_mode: Optional[str] = None
#     master_pattern: List[List[str]]  # 改为字符串类型
#     game_config_dump: Dict[str, Any]
#     players: List[GamePlayerDetailResponse]
#
#
# # --- LLM Player 核心功能 ---
#
# def build_llm_player_prompt(
#         grid_size: int,
#         symbols_in_use: List[Symbol],
#         current_grid: List[List[str]],  # 改为接受字符串网格
#         turn_number: int,
#         player_name: str
# ) -> str:
#     """构建LLM玩家的完整提示词"""
#
#     symbols_str = ", ".join(symbols_in_use)
#     grid_display = format_grid_for_display(current_grid, grid_size)
#     total_cells = grid_size * grid_size
#     observed_cells = sum(1 for row in current_grid for cell in row if cell != "?" and cell is not None)
#     unknown_cells = total_cells - observed_cells
#
#     system_prompt = f"""You are {player_name}, a scientist playing Patterns II, a logic puzzle game.
# 你的目标是尽可能的通过最少的观察进行推理pattern，以得到最高分。
# GAME RULES:
# - Grid size: {grid_size}×{grid_size}
# - Available symbols: {symbols_str}
# - Goal: Deduce the hidden pattern through strategic observation and logical reasoning, and try to get the highest score.
# - Scoring: +1 for each correct unobserved cell, -1 for each incorrect unobserved cell, 0 for observed cells
#
# CURRENT GAME STATE:
# Turn: {turn_number}
# Observed cells: {observed_cells}/{total_cells}
# Unknown cells: {unknown_cells}
#
# Current Grid (? = unknown):
# {grid_display}
#
# YOUR AVAILABLE ACTIONS:
# 1. OBSERVE: Request to see specific cells (up to 3 cells per turn)
#    - Use when you need more information to form hypotheses
#
# 2. GUESS: Submit a complete {grid_size}×{grid_size} grid as your hypothesis
#    - Use when you're confident about the pattern
#    - This will be your final answer and end the game
#
# 3. GIVE_UP: Forfeit the game (score = 0, avoid negative scores)
#    - Use only if the pattern seems impossible to deduce
#
# STRATEGY GUIDELINES:
# - Look for patterns: symmetry, repetition, gradients, geometric shapes
# - Observe key positions first: corners, center, symmetry axes
# - Form hypotheses early and test them with targeted observations
# - Balance exploration (observing) vs exploitation (guessing)
# - Consider the symbol distribution and frequency
#
# RESPONSE FORMAT:
# You must respond with a JSON object containing:
# {{
#   "action": "observe" | "guess" | "give_up",
#   "cellsToObserve": [optional, for observe action: [{{"row": 0, "col": 1}}, ...]],
#   "guessGrid": [optional, for guess action: complete {grid_size}×{grid_size} grid],
#   "reasoning": "Your detailed thought process and strategy",
#   "confidence": 0.85 [optional, 0-1 scale for guess actions]
# }}
#
# IMPORTANT:
# - Only use symbols from the available set: {symbols_str}
# - Row indices: 0 to {grid_size - 1}, Column indices: 0 to {grid_size - 1}
# - For guessGrid, provide a complete {grid_size}×{grid_size} array of symbols
# - Be strategic: don't observe randomly, look for patterns first
# - Explain your reasoning clearly
#
# Now analyze the current grid and decide your next action:"""
#
#     return system_prompt
#
#
# def format_grid_for_display(grid: List[List[str]], grid_size: int) -> str:
#     """将网格格式化为易读的显示格式"""
#     if not grid or len(grid) == 0:
#         return "Empty grid"
#
#     # 创建列标题
#     header = "    " + "   ".join(chr(65 + i) for i in range(grid_size))
#     lines = [header]
#
#     # 添加分隔线
#     separator = "  " + "---" * grid_size + "-" * (grid_size - 1)
#     lines.append(separator)
#
#     # 添加每一行
#     for i in range(grid_size):
#         if i < len(grid):
#             row_data = []
#             for j in range(grid_size):
#                 if j < len(grid[i]):
#                     cell = grid[i][j]
#                     if cell is None or cell == "?" or cell == "null" or cell == "undefined":
#                         row_data.append(" ? ")
#                     else:
#                         row_data.append(f" {cell} ")
#                 else:
#                     row_data.append(" ? ")
#             row_str = f"{i + 1} |" + "|".join(row_data) + "|"
#         else:
#             row_str = f"{i + 1} |" + "|".join([" ? "] * grid_size) + "|"
#         lines.append(row_str)
#
#     return "\n".join(lines)
#
#
# def parse_llm_response(response_text: str, grid_size: int, symbols_in_use: List[Symbol]) -> LLMPlayerTurnResponse:
#     """解析LLM的JSON响应"""
#     try:
#         response_data = json.loads(response_text)
#     except json.JSONDecodeError as e:
#         raise ValueError(f"Invalid JSON response: {e}")
#
#     action = response_data.get("action")
#     if action not in ["observe", "guess", "give_up"]:
#         raise ValueError(f"Invalid action: {action}")
#
#     reasoning = response_data.get("reasoning", "No reasoning provided")
#     confidence = response_data.get("confidence")
#
#     cells_to_observe = None
#     guess_grid = None
#
#     if action == "observe":
#         cells_data = response_data.get("cellsToObserve", [])
#         cells_to_observe = []
#
#         for cell_data in cells_data:
#             if isinstance(cell_data, dict) and "row" in cell_data and "col" in cell_data:
#                 row, col = cell_data["row"], cell_data["col"]
#                 if 0 <= row < grid_size and 0 <= col < grid_size:
#                     cells_to_observe.append(PositionModel(row=row, col=col))
#
#         if not cells_to_observe:
#             raise ValueError("Observe action requires valid cells to observe")
#
#     elif action == "guess":
#         guess_data = response_data.get("guessGrid")
#         if not guess_data:
#             raise ValueError("Guess action requires guessGrid")
#
#         # 验证猜测网格
#         if not isinstance(guess_data, list) or len(guess_data) != grid_size:
#             raise ValueError(f"GuessGrid must be a {grid_size}×{grid_size} array")
#
#         guess_grid = []
#         for i, row in enumerate(guess_data):
#             if not isinstance(row, list) or len(row) != grid_size:
#                 raise ValueError(f"Row {i} must have {grid_size} elements")
#
#             validated_row = []
#             for j, cell in enumerate(row):
#                 if cell not in symbols_in_use:
#                     raise ValueError(f"Invalid symbol '{cell}' at position ({i},{j})")
#                 validated_row.append(cell)
#             guess_grid.append(validated_row)
#
#     return LLMPlayerTurnResponse(
#         action=action,
#         cellsToObserve=cells_to_observe,
#         guessGrid=guess_grid,
#         reasoning=reasoning,
#         confidence=confidence
#     )
#
#
# def validate_pattern(pattern: Any, expected_size: int, valid_symbols: List[str]) -> Grid:
#     """验证LLM生成的模式是否符合要求"""
#     if not isinstance(pattern, list) or len(pattern) != expected_size:
#         raise ValueError(f"Pattern must be a {expected_size}x{expected_size} list")
#
#     validated_grid: Grid = []
#     for row_idx, row in enumerate(pattern):
#         if not isinstance(row, list) or len(row) != expected_size:
#             raise ValueError(f"Row {row_idx} must have {expected_size} elements")
#
#         validated_row: List[Cell] = []
#         for col_idx, cell in enumerate(row):
#             if cell not in valid_symbols:
#                 raise ValueError(f"Invalid symbol '{cell}' at position ({row_idx},{col_idx})")
#             validated_row.append(cell)
#         validated_grid.append(validated_row)
#
#     return validated_grid
#
#
# def build_system_prompt(grid_size: int, num_symbols: int, available_symbols: List[str]) -> str:
#     """构建LLM设计模式的系统提示词"""
#     symbols_str = ", ".join(available_symbols)
#
#     return f"""你是一个逻辑谜题设计专家。请为"Patterns II"游戏设计一个有趣且具有挑战性的模式。
#
# 游戏规则:
# - 网格大小: {grid_size}x{grid_size}
# - 可用符号: {symbols_str} (共{num_symbols}个)
# - 玩家需要通过观察部分单元格来推断完整模式
#
# 设计要求:
# 1. 模式应该有逻辑性和可推断性
# 2. 不要过于简单（如全部相同符号）
# 3. 也不要完全随机无规律
# 4. 考虑使用对称、重复、渐变等设计原则
# 5. 确保玩家能通过观察少量单元格推断出规律
#
# 请返回JSON格式:
# {{
#   "pattern": [
#     [符号行1],
#     [符号行2],
#     ...
#   ],
#   "description": "模式的设计思路说明"
# }}
#
# 注意: 只使用提供的符号，确保数组维度正确为{grid_size}x{grid_size}。"""
#
#
# def build_user_prompt(user_prompt: Optional[str]) -> str:
#     """构建用户自定义提示词"""
#     if user_prompt and user_prompt.strip():
#         return f"用户要求: {user_prompt.strip()}\n\n请在满足基本要求的同时，尽量体现用户的特殊要求。"
#     return "Please design a pattern that meets the basic requirements of the game."
#
#
# # --- 数据库连接 ---
# DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/patterns_db")
# db_pool = None
#
# # CORS配置
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["http://localhost:3000", "https://*.vercel.app"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )
#
#
# # --- API端点实现 ---
#
# @app.post("/api/llm-player-turn", response_model=LLMPlayerTurnResponse)
# async def llm_player_turn_endpoint(request: LLMPlayerTurnRequest):
#     """LLM玩家单次行动API"""
#     logger.info(f"LLM玩家 {request.playerName} 第{request.turnNumber}回合开始")
#
#     # 验证OpenAI API密钥
#     if not openai_client.api_key:
#         logger.error("OpenAI API密钥未配置")
#         raise HTTPException(
#             status_code=503,
#             detail="OpenAI API密钥未配置，请检查服务器环境变量"
#         )
#
#     try:
#         # 构建提示词
#         prompt = build_llm_player_prompt(
#             request.gridSize,
#             request.symbolsInUse,
#             request.currentGrid,
#             request.turnNumber,
#             request.playerName
#         )
#
#         logger.info(f"调用OpenAI API，模型: {request.llmModel}")
#
#         # 调用OpenAI API
#         response = await asyncio.to_thread(
#             openai_client.chat.completions.create,
#             model=request.llmModel or "gpt-4o",
#             messages=[{"role": "user", "content": prompt}],
#             response_format={"type": "json_object"},
#             temperature=0.3,
#             max_tokens=2000
#         )
#
#         # 解析响应
#         content = response.choices[0].message.content
#         if not content:
#             raise HTTPException(status_code=500, detail="LLM返回空响应")
#
#         logger.info(f"LLM原始响应: {content[:200]}...")
#
#         # 解析LLM响应
#         try:
#             parsed_response = parse_llm_response(
#                 content,
#                 request.gridSize,
#                 request.symbolsInUse
#             )
#         except ValueError as e:
#             logger.error(f"LLM响应解析失败: {e}")
#             raise HTTPException(status_code=500, detail=f"LLM响应格式错误: {str(e)}")
#
#         logger.info(f"LLM决策: {parsed_response.action}, 推理: {parsed_response.reasoning[:100]}...")
#
#         return parsed_response
#
#     except openai.APIError as e:
#         logger.error(f"OpenAI API错误: {e}")
#         error_message = str(e)
#
#         if "insufficient_quota" in error_message.lower():
#             error_message = "OpenAI API配额不足"
#         elif "rate_limit" in error_message.lower():
#             error_message = "API调用频率过高，请稍后重试"
#
#         raise HTTPException(status_code=502, detail=f"OpenAI API错误: {error_message}")
#
#     except HTTPException:
#         raise
#
#     except Exception as e:
#         logger.error(f"LLM玩家回合处理失败: {e}")
#         raise HTTPException(status_code=500, detail=f"LLM玩家回合处理失败: {str(e)}")
#
#
# @app.post("/api/design-pattern", response_model=DesignPatternResponse)
# async def design_pattern_endpoint(request: DesignPatternRequest):
#     """使用LLM生成游戏模式"""
#     if not openai_client.api_key:
#         raise HTTPException(status_code=503, detail="OpenAI API密钥未配置")
#
#     current_symbols = ALL_SYMBOLS_PY[:request.numSymbols]
#     system_prompt = build_system_prompt(request.gridSize, request.numSymbols, current_symbols)
#     user_prompt = build_user_prompt(request.prompt)
#
#     try:
#         response = await asyncio.to_thread(
#             openai_client.chat.completions.create,
#             model=request.llmModel or "gpt-3.5-turbo",
#             messages=[
#                 {"role": "system", "content": system_prompt},
#                 {"role": "user", "content": user_prompt}
#             ],
#             response_format={"type": "json_object"},
#             temperature=0.7,
#             max_tokens=1000
#         )
#
#         content = response.choices[0].message.content
#         print(content)
#         if not content:
#             raise HTTPException(status_code=500, detail="LLM返回空响应")
#
#         parsed_response = json.loads(content)
#         llm_pattern = parsed_response.get("pattern")
#
#         validated_pattern = validate_pattern(llm_pattern, request.gridSize, current_symbols)
#         return DesignPatternResponse(pattern=validated_pattern)
#
#     except Exception as e:
#         logger.error(f"Pattern design failed: {e}")
#         raise HTTPException(status_code=500, detail=f"Pattern design failed: {e}")
#
#
# @app.post("/api/games", response_model=GameCreateResponse, status_code=201)
# async def save_game_endpoint(request: GameCreateRequest):
#     """保存完成的游戏数据"""
#     if not db_pool:
#         raise HTTPException(status_code=500, detail="Database not connected")
#
#     try:
#         async with db_pool.acquire() as conn:
#             game_id = str(uuid.uuid4())
#             await conn.execute(
#                 """
#                 INSERT INTO games (id, grid_size, num_symbols, designer_type, designer_llm_model, designer_pattern_mode,
#                                    master_pattern, game_config_dump)
#                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
#                 """,
#                 game_id, request.grid_size, request.num_symbols, request.designer_type,
#                 request.designer_llm_model, request.designer_pattern_mode,
#                 json.dumps(request.master_pattern), json.dumps(request.game_config_dump)
#             )
#
#             for player in request.players:
#                 # 安全地处理 queried_cells
#                 queried_cells_json = None
#                 if player.queried_cells:
#                     try:
#                         queried_cells_json = json.dumps([{"row": p.row, "col": p.col} for p in player.queried_cells])
#                     except Exception as e:
#                         logger.warning(
#                             f"Failed to serialize queried_cells for player {player.player_name_in_game}: {e}")
#                         queried_cells_json = None
#
#                 await conn.execute(
#                     """
#                     INSERT INTO game_players (game_id, player_name_in_game, player_type, player_llm_model, final_score,
#                                               final_guess, action_log, queried_cells)
#                     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
#                     """,
#                     game_id, player.player_name_in_game, player.player_type,
#                     player.player_llm_model, player.final_score,
#                     json.dumps(player.final_guess) if player.final_guess else None,
#                     json.dumps(player.action_log) if player.action_log else None,
#                     queried_cells_json
#                 )
#
#             logger.info(f"Game saved successfully with ID: {game_id}")
#             return GameCreateResponse(message="Game saved successfully", game_id=game_id)
#
#     except Exception as e:
#         logger.error(f"Failed to save game: {e}")
#         raise HTTPException(status_code=500, detail=f"Failed to save game: {str(e)}")
#
#
# @app.get("/api/games")
# async def get_games_list_endpoint(limit: int = 50, offset: int = 0):
#     """获取游戏历史列表 - 返回原始字典而不是Pydantic模型"""
#     if not db_pool:
#         raise HTTPException(status_code=500, detail="Database not connected")
#
#     try:
#         async with db_pool.acquire() as conn:
#             rows = await conn.fetch(
#                 "SELECT id, created_at, grid_size, num_symbols, designer_type FROM games ORDER BY created_at DESC LIMIT $1 OFFSET $2",
#                 limit, offset
#             )
#
#             # 直接返回字典列表，确保UUID被正确转换
#             games = []
#             for row in rows:
#                 game_dict = {
#                     'id': str(row['id']),  # 强制转换UUID为字符串
#                     'created_at': row['created_at'].isoformat(),  # 转换datetime为ISO字符串
#                     'grid_size': row['grid_size'],
#                     'num_symbols': row['num_symbols'],
#                     'designer_type': row['designer_type']
#                 }
#                 games.append(game_dict)
#
#             logger.info(f"Retrieved {len(games)} games from database")
#             return games
#
#     except Exception as e:
#         logger.error(f"Failed to get games list: {e}")
#         raise HTTPException(status_code=500, detail=f"Failed to get games list: {str(e)}")
#
#
# @app.get("/api/games/{game_id}")
# async def get_game_details_endpoint(game_id: str):
#     """获取特定游戏的详细信息 - 返回原始字典"""
#     if not db_pool:
#         raise HTTPException(status_code=500, detail="Database not connected")
#
#     try:
#         async with db_pool.acquire() as conn:
#             game_row = await conn.fetchrow("SELECT * FROM games WHERE id = $1", game_id)
#             if not game_row:
#                 raise HTTPException(status_code=404, detail="Game not found")
#
#             player_rows = await conn.fetch("SELECT * FROM game_players WHERE game_id = $1 ORDER BY final_score DESC",
#                                            game_id)
#
#             players = []
#             for row in player_rows:
#                 player_data = {
#                     'player_name_in_game': row['player_name_in_game'],
#                     'player_type': row['player_type'],
#                     'player_llm_model': row['player_llm_model'],
#                     'final_score': row['final_score'] or 0,
#                     'final_guess': None,
#                     'action_log': None,
#                     'queried_cells': []
#                 }
#
#                 # 安全地解析JSON字段
#                 if row['final_guess']:
#                     try:
#                         player_data['final_guess'] = json.loads(row['final_guess'])
#                     except Exception as e:
#                         logger.warning(f"Failed to parse final_guess for player: {e}")
#
#                 if row['action_log']:
#                     try:
#                         player_data['action_log'] = json.loads(row['action_log'])
#                     except Exception as e:
#                         logger.warning(f"Failed to parse action_log for player: {e}")
#
#                 # 特别处理 queried_cells
#                 if row['queried_cells']:
#                     try:
#                         queried_data = json.loads(row['queried_cells'])
#                         player_data['queried_cells'] = queried_data  # 直接使用解析后的数据
#                     except Exception as e:
#                         logger.warning(f"Failed to parse queried_cells: {e}")
#                         player_data['queried_cells'] = []
#
#                 players.append(player_data)
#
#             # 构建游戏数据字典
#             game_data = {
#                 'id': str(game_row['id']),  # 强制转换UUID为字符串
#                 'created_at': game_row['created_at'].isoformat(),  # 转换datetime为ISO字符串
#                 'grid_size': game_row['grid_size'],
#                 'num_symbols': game_row['num_symbols'],
#                 'designer_type': game_row['designer_type'],
#                 'designer_llm_model': game_row['designer_llm_model'],
#                 'designer_pattern_mode': game_row['designer_pattern_mode'],
#                 'master_pattern': json.loads(game_row['master_pattern']),
#                 'game_config_dump': json.loads(game_row['game_config_dump']),
#                 'players': players
#             }
#
#             return game_data
#
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Failed to get game details: {e}")
#         raise HTTPException(status_code=500, detail=f"Failed to get game details: {str(e)}")
#
#
# @app.get("/health")
# async def health_check():
#     """健康检查"""
#     db_status = "connected" if db_pool else "disconnected"
#     openai_status = "configured" if openai_client.api_key else "not configured"
#
#     return {
#         "status": "healthy",
#         "openai_configured": bool(openai_client.api_key),
#         "database_connected": bool(db_pool),
#         "database_status": db_status,
#         "openai_status": openai_status
#     }
#
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
import os
import uuid
import json
import logging
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Literal, Dict, Any, Union
import asyncpg
from contextlib import asynccontextmanager
import openai
from dotenv import load_dotenv
import threading
from collections import defaultdict

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化OpenAI客户端
openai_client = openai.OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# 全局chat history存储 - 线程安全
chat_histories_lock = threading.Lock()
chat_histories: Dict[str, List[Dict[str, str]]] = defaultdict(list)


# --- 全局异常处理器 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时创建数据库连接池
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("数据库连接池创建成功")

        # 自动创建表（如果不存在）
        await create_tables_if_not_exist()

    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        db_pool = None

    yield

    # 关闭时清理连接池
    if db_pool:
        await db_pool.close()
        logger.info("数据库连接池已关闭")


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
                                       game_config_dump          JSONB
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

                # 创建索引
                await conn.execute("CREATE INDEX idx_game_players_game_id ON game_players(game_id)")
                await conn.execute("CREATE INDEX idx_games_created_at ON games(created_at DESC)")

                logger.info("数据库表创建成功！")
            else:
                logger.info("数据库表已存在")

    except Exception as e:
        logger.error(f"创建数据库表失败: {e}")


# --- FastAPI应用初始化 ---
app = FastAPI(title="Patterns II Game Backend", lifespan=lifespan, redirect_slashes=False)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://*.vercel.app", "https://patterns2.vercel.app","https://www.haozhang.site"],
    allow_credentials=True,
    allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

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


class OpenAIModel(BaseModel):
    id: str
    object: str
    created: int
    owned_by: str


class ModelsListResponse(BaseModel):
    object: str
    data: List[OpenAIModel]


# 新增：LLM模型参数配置
class LLMModelParams(BaseModel):
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    maxTokens: Optional[int] = Field(None, ge=1, le=4096)
    topP: Optional[float] = Field(None, ge=0.0, le=1.0)
    frequencyPenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    presencePenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)


# 修复后的LLM Player API模型
class LLMPlayerTurnRequest(BaseModel):
    """LLM玩家单次行动请求"""
    playerId: str
    playerName: str
    gameId: str  # 用��区分不同游戏的chat history
    gridSize: int
    symbolsInUse: List[Symbol]
    currentGrid: List[List[str]]
    llmModel: Optional[str] = "chatgpt-4o-latest"
    llmModelParams: Optional[LLMModelParams] = None  # 新增：模型参数
    turnNumber: int = 1

    @validator('currentGrid')
    def validate_grid(cls, v, values):
        """验证网格格式"""
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
    llmModel: Optional[str] = "chatgpt-4o-latest"
    llmModelParams: Optional[LLMModelParams] = None  # 新增：模型参数
    prompt: Optional[str] = None


class DesignPatternResponse(BaseModel):
    pattern: Grid


# 游戏保存相关模型
class PlayerStateInGame(BaseModel):
    player_name_in_game: str
    player_type: Literal["Human", "LLM"]
    player_llm_model: Optional[str] = None
    player_llm_model_params: Optional[LLMModelParams] = None  # 新增：模型参数
    final_score: int
    final_guess: Optional[List[List[str]]] = None
    action_log: Optional[List[str]] = None
    queried_cells: Optional[List[PositionModel]] = None


class GameCreateRequest(BaseModel):
    grid_size: int
    num_symbols: int
    designer_type: Literal["Human", "LLM"]
    designer_llm_model: Optional[str] = None
    designer_llm_model_params: Optional[LLMModelParams] = None  # 新增：设计师模型参数
    designer_pattern_mode: Optional[str] = None
    master_pattern: List[List[str]]
    game_config_dump: Dict[str, Any]
    players: List[PlayerStateInGame]


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
    player_llm_model_params: Optional[LLMModelParams] = None  # 新增：模型参数
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
    designer_llm_model_params: Optional[LLMModelParams] = None  # 新增：设计师模型参数
    designer_pattern_mode: Optional[str] = None
    master_pattern: List[List[str]]
    game_config_dump: Dict[str, Any]
    players: List[GamePlayerDetailResponse]


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
- Goal: Deduce the hidden pattern through strategic observation and logical reasoning, and try your best to make a confident guess with the least number of observations and get the highest score.
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

def build_openai_params(model_params: Optional[LLMModelParams]) -> Dict[str, Any]:
    """构建OpenAI API调用参数"""
    params = {}
    if model_params:
        if model_params.temperature is not None:
            params["temperature"] = model_params.temperature
        if model_params.maxTokens is not None:
            params["max_tokens"] = model_params.maxTokens
        if model_params.topP is not None:
            params["top_p"] = model_params.topP
        if model_params.frequencyPenalty is not None:
            params["frequency_penalty"] = model_params.frequencyPenalty
        if model_params.presencePenalty is not None:
            params["presence_penalty"] = model_params.presencePenalty

    # 设置默认值
    if "temperature" not in params:
        params["temperature"] = 0.3
    if "max_tokens" not in params:
        params["max_tokens"] = 2000

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
    """验证LLM生成的模式是否符合要求 - 支持字符串和数组格式"""
    if not isinstance(pattern, list) or len(pattern) != expected_size:
        raise ValueError(f"Pattern must be a {expected_size}x{expected_size} list")

    validated_grid: Grid = []
    for row_idx, row in enumerate(pattern):
        validated_row: List[Cell] = []

        # 处理字符串格式（GPT-4o 可能返回的格式）
        if isinstance(row, str):
            if len(row) != expected_size:
                raise ValueError(f"Row {row_idx} must have {expected_size} characters, got {len(row)}")

            for col_idx, char in enumerate(row):
                if char not in valid_symbols:
                    raise ValueError(f"Invalid symbol '{char}' at position ({row_idx},{col_idx})")
                validated_row.append(char)

        # 处理数组格式（GPT-3.5-turbo 返回的格式）
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


# def build_system_prompt(grid_size: int, num_symbols: int, available_symbols: List[str]) -> str:
#     """构建LLM设计模式的系统提示词 - 明确指定返回格式"""
#     symbols_str = ", ".join(available_symbols)
#     return f"""Design a pattern for "Patterns II".
#
# REQUIREMENTS:
# - Grid: {grid_size}x{grid_size}
# - Available symbols: {symbols_str}
# - Create an interesting, logical pattern
#
# IMPORTANT - RESPONSE FORMAT:
# You must return a JSON object with this EXACT structure:
# {{
#   "pattern": [
#     ["{available_symbols[0]}", "{available_symbols[1]}", ...],
#     ["{available_symbols[1]}", "{available_symbols[2]}", ...],
#     ...
#   ],
#   "description": "Brief description of the pattern"
# }}
#
# CRITICAL: The "pattern" field must be an array of arrays (2D array), NOT an array of strings.
# Each inner array represents a row and must contain exactly {grid_size} symbol strings.
# Use only the provided symbols: {symbols_str}"""
#
#
# def build_user_prompt(user_prompt: Optional[str]) -> str:
#     """构建用户自定义提示词"""
#     base_prompt = "Create a pattern that is interesting but solvable through logical deduction."
#     if user_prompt and user_prompt.strip():
#         return f"{base_prompt} User requirement: {user_prompt.strip()}"
#     return base_prompt

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

# --- 数据库连接 ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/patterns_db")
db_pool = None

# --- API端点实现 ---

@app.get("/api/models", response_model=ModelsListResponse)
async def get_available_models():
    """获取可用的OpenAI模型列表"""
    if not openai_client.api_key:
        raise HTTPException(status_code=503, detail="OpenAI API密钥未配置")

    try:
        logger.info("从OpenAI API获取模型列表...")

        # 调用OpenAI API获取模型列表
        response = await asyncio.to_thread(openai_client.models.list)

        # 过滤出GPT模型，排除其他类型的模型
        gpt_models = []
        for model in response.data:
            model_id = model.id.lower()
            # 只包含GPT模型，排除embedding、whisper等其他模型
            if any(keyword in model_id for keyword in ['gpt-4', 'gpt-3.5', 'chatgpt']):
                gpt_models.append(OpenAIModel(
                    id=model.id,
                    object=model.object,
                    created=model.created,
                    owned_by=model.owned_by
                ))

        # 按模型名称排序，优先显示常用模型
        priority_models = ['chatgpt-4o-latest', 'gpt-4o', 'gpt-4o-mini', 'gpt-4', 'gpt-4-turbo', 'gpt-3.5-turbo']

        def sort_key(model):
            if model.id in priority_models:
                return (0, priority_models.index(model.id))
            return (1, model.id)

        gpt_models.sort(key=sort_key)

        logger.info(f"获取到 {len(gpt_models)} 个GPT模型")

        # 返回符合OpenAI API格式的响应
        return ModelsListResponse(
            object="list",
            data=gpt_models
        )

    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        # 如果API调用失败，返回默认模型列表
        default_models = [
            OpenAIModel(id="chatgpt-4o-latest", object="model", created=0, owned_by="openai"),
            OpenAIModel(id="gpt-4o", object="model", created=0, owned_by="openai"),
            OpenAIModel(id="gpt-4o-mini", object="model", created=0, owned_by="openai"),
            OpenAIModel(id="gpt-4", object="model", created=0, owned_by="openai"),
            OpenAIModel(id="gpt-4-turbo", object="model", created=0, owned_by="openai"),
            OpenAIModel(id="gpt-3.5-turbo", object="model", created=0, owned_by="openai"),
        ]
        return ModelsListResponse(
            object="list",
            data=default_models
        )


@app.post("/api/llm-player-turn", response_model=LLMPlayerTurnResponse)
async def llm_player_turn_endpoint(request: LLMPlayerTurnRequest):
    """LLM玩家单次行动API - 支持多轮对话和模型参数"""
    logger.info(f"LLM玩家 {request.playerName} 第{request.turnNumber}回合开始")

    # 验证OpenAI API密钥
    if not openai_client.api_key:
        logger.error("OpenAI API密钥未配置")
        raise HTTPException(
            status_code=503,
            detail="OpenAI API密钥未配置，请检查服务器环境变量"
        )

    try:
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

        # 构建OpenAI API参数
        api_params = build_openai_params(request.llmModelParams)

        logger.info(f"调用OpenAI API，模型: {request.llmModel}, 参数: {api_params}, 历史消息数: {len(messages)}")

        # 调用OpenAI API
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model=request.llmModel or "chatgpt-4o-latest",
            messages=messages,
            response_format={"type": "json_object"},
            **api_params
        )

        # 解析响应
        content = response.choices[0].message.content
        if not content:
            raise HTTPException(status_code=500, detail="LLM返回空响应")

        # 将LLM响应添加到对话历史
        append_message(request.gameId, request.playerId, "assistant", content)

        logger.info(f"LLM原始响应: {content[:200]}...")

        # 解析LLM响应
        try:
            parsed_response = parse_llm_response(
                content,
                request.gridSize,
                request.symbolsInUse
            )
        except ValueError as e:
            logger.error(f"LLM响应解析失败: {e}")
            raise HTTPException(status_code=500, detail=f"LLM响应格式错误: {str(e)}")

        logger.info(f"LLM决策: {parsed_response.action}, 推理: {parsed_response.reasoning[:100]}...")

        return parsed_response

    except openai.APIError as e:
        logger.error(f"OpenAI API错误: {e}")
        error_message = str(e)

        if "insufficient_quota" in error_message.lower():
            error_message = "OpenAI API配额不足"
        elif "rate_limit" in error_message.lower():
            error_message = "API调用频率过高，请稍后重试"

        raise HTTPException(status_code=502, detail=f"OpenAI API错误: {error_message}")

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"LLM玩家回合处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"LLM玩家回合处理失败: {str(e)}")


@app.post("/api/design-pattern", response_model=DesignPatternResponse)
async def design_pattern_endpoint(request: DesignPatternRequest):
    """使用LLM生成游戏模式"""
    if not openai_client.api_key:
        raise HTTPException(status_code=503, detail="OpenAI API密钥未配置")

    current_symbols = ALL_SYMBOLS_PY[:request.numSymbols]
    system_prompt = build_system_prompt(request.gridSize, request.numSymbols, current_symbols)
    user_prompt = build_user_prompt(request.prompt)

    # 构建API参数
    api_params = build_openai_params(request.llmModelParams)

    # 对于设计模式，使用稍高的temperature以增加创造性
    if "temperature" not in api_params or api_params["temperature"] < 0.5:
        api_params["temperature"] = 0.7

    # print("System prompt:"+ system_prompt)
    # print("User prompt:"+ user_prompt)

    try:
        logger.info(f"调用OpenAI API生成模式，模型: {request.llmModel}, 参数: {api_params}")

        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model=request.llmModel or "chatgpt-4o-latest",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            **api_params
        )
        # print("response:", response)

        content = response.choices[0].message.content
        if not content:
            raise HTTPException(status_code=500, detail="LLM返回空响应")

        logger.info(f"LLM模式设计响应: {content[:200]}...")

        parsed_response = json.loads(content)
        llm_pattern = parsed_response.get("pattern")
        description = parsed_response.get("description", "无描述")

        if not llm_pattern:
            raise ValueError("LLM响应中缺少pattern字段")

        logger.info(f"LLM设计思路: {description}")

        # 使用改进的验证函数，支持字符串和数组格式
        validated_pattern = validate_pattern(llm_pattern, request.gridSize, current_symbols)

        logger.info(f"模式验证成功，大小: {len(validated_pattern)}x{len(validated_pattern[0])}")

        return DesignPatternResponse(pattern=validated_pattern)

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
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
            game_id = str(uuid.uuid4())

            # 序列化设计师模型参数
            designer_params_json = None
            if request.designer_llm_model_params:
                designer_params_json = json.dumps(request.designer_llm_model_params.dict(exclude_none=True))

            await conn.execute(
                """
                INSERT INTO games (id, grid_size, num_symbols, designer_type, designer_llm_model,
                                   designer_llm_model_params, designer_pattern_mode, master_pattern, game_config_dump)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                game_id, request.grid_size, request.num_symbols, request.designer_type,
                request.designer_llm_model, designer_params_json, request.designer_pattern_mode,
                json.dumps(request.master_pattern), json.dumps(request.game_config_dump)
            )

            for player in request.players:
                # 安全地处理 queried_cells
                queried_cells_json = None
                if player.queried_cells:
                    try:
                        queried_cells_json = json.dumps([{"row": p.row, "col": p.col} for p in player.queried_cells])
                    except Exception as e:
                        logger.warning(
                            f"Failed to serialize queried_cells for player {player.player_name_in_game}: {e}")
                        queried_cells_json = None

                # 序列化玩家模型参数
                player_params_json = None
                if player.player_llm_model_params:
                    player_params_json = json.dumps(player.player_llm_model_params.dict(exclude_none=True))

                await conn.execute(
                    """
                    INSERT INTO game_players (game_id, player_name_in_game, player_type, player_llm_model,
                                              player_llm_model_params, final_score, final_guess, action_log,
                                              queried_cells)
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

    except Exception as e:
        logger.error(f"Failed to save game: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save game: {str(e)}")


@app.get("/api/games")
async def get_games_list_endpoint(limit: int = 50, offset: int = 0):
    """获取游戏历史列表"""
    if not db_pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with db_pool.acquire() as conn:
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


@app.get("/health")
async def health_check():
    """健康检查"""
    db_status = "connected" if db_pool else "disconnected"
    openai_status = "configured" if openai_client.api_key else "not configured"

    return {
        "status": "healthy",
        "openai_configured": bool(openai_client.api_key),
        "database_connected": bool(db_pool),
        "database_status": db_status,
        "openai_status": openai_status
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

