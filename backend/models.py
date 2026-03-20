"""Pydantic models for the Patterns2 API."""

from datetime import datetime
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, field_validator

# Type aliases
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


class LLMModelParams(BaseModel):
    model_config = {"protected_namespaces": ()}

    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    maxCompletionTokens: Optional[int] = Field(None, ge=1, le=65536)
    topP: Optional[float] = Field(None, ge=0.0, le=1.0)
    frequencyPenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    presencePenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    reasoningEffort: Optional[str] = None  # xhigh/high/medium/low/minimal/none
    chatHistoryEnabled: Optional[bool] = False  # Multi-turn chat history, default off (single-turn)


class LLMPlayerTurnRequest(BaseModel):
    """LLM player single turn request."""
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
        """Validate grid format."""
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
    """LLM player single turn response."""
    action: Literal["observe", "guess", "final_guess", "give_up"]
    cellsToObserve: Optional[List[PositionModel]] = None
    guessGrid: Optional[Grid] = None
    reasoning: str = ""
    confidence: Optional[float] = None
    input_tokens: Optional[int] = 0
    output_tokens: Optional[int] = 0


class DesignPatternRequest(BaseModel):
    gridSize: int = Field(..., ge=3, le=6)
    numSymbols: int = Field(..., ge=2, le=len(ALL_SYMBOLS_PY))
    llmModel: Optional[str] = "openai_official/chatgpt-4o-latest"
    llmModelParams: Optional[LLMModelParams] = None
    prompt: Optional[str] = None


class DesignPatternResponse(BaseModel):
    pattern: Grid
    description: Optional[str] = None


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


class EvolvingConfig(BaseModel):
    """Per-participant evolving configuration."""
    enabled: bool = False
    mode: str = "fresh"  # "fresh" = start from empty history; "imported" = import from existing games
    import_game_ids: Optional[List[str]] = None  # game IDs to import history from (imported mode)
    accumulate: bool = True  # imported mode: True = keep adding new games, False = fixed imported history only


class TestSetParticipant(BaseModel):
    model_config = {"protected_namespaces": ()}

    participant_type: Literal["Human", "LLM"] = "LLM"
    human_name: Optional[str] = None  # required for Human participants (unique nickname)
    model_name: Optional[str] = None  # optional for Human participants
    model_params: Optional[LLMModelParams] = None
    evolving_config: Optional[EvolvingConfig] = None


class HumanPlayerActionRequest(BaseModel):
    """Request from a human player during test set execution."""
    game_id: str
    player_id: str
    action: Literal["observe", "guess", "give_up"]
    cells_to_observe: Optional[List[PositionModel]] = None  # max 3 cells
    guess_grid: Optional[List[List[str]]] = None
    session_token: Optional[str] = None  # optional session validation for human testing


class JoinRequest(BaseModel):
    player_name: str


class JoinResponse(BaseModel):
    session_token: str
    participant_index: int
    test_set_id: str
    player_name: str


class ScoreboardEntry(BaseModel):
    name: str
    participant_index: int
    current_game_index: int
    total_games: int
    cumulative_score: float
    is_finished: bool


class ScoreboardResponse(BaseModel):
    players: list[ScoreboardEntry]


class TestSetGameConfig(BaseModel):
    grid_size: int = Field(default=6, ge=3, le=6)
    num_symbols: int = Field(default=5, ge=2, le=6)
    optional_prompt: Optional[str] = None
    custom_pattern: Optional[List[List[str]]] = None
    repeat_count: int = Field(default=1, ge=1, le=10)
    pattern_mode: Optional[str] = None
    symmetry_type: Optional[str] = None
    shift_step: Optional[int] = None
    llm_pattern_model: Optional[str] = None
    llm_pattern_model_params: Optional[LLMModelParams] = None
    llm_pattern_prompt: Optional[str] = None
    llm_designed_pattern: Optional[List[List[str]]] = None


class TestSetCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    participants: List[TestSetParticipant]
    llm_rotate_designer: bool = True
    games: List[TestSetGameConfig]
    enable_human_test: bool = False


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
    join_token: Optional[str] = None


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
    wins_as_player: int = 0
    wins_as_designer: int = 0
    total_games: int
    overall_win_rate: float = 0.0
    overall_wins: int = 0
    cost_per_game: float = 0.0
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_score_as_player: int = 0
    theoretical_max_scientist_score: int = 0
    total_score_as_designer: float = 0.0


class LeaderboardResponse(BaseModel):
    test_set_id: Optional[str]
    test_set_name: Optional[str]
    entries: List[LeaderboardEntry]
    generated_at: datetime
