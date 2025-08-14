-- Enable UUID generation if not already enabled
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Games Table
CREATE TABLE games (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    grid_size INTEGER NOT NULL,
    num_symbols INTEGER NOT NULL,
    designer_type TEXT NOT NULL, -- "Human" or "LLM"
    designer_llm_model TEXT,
    designer_llm_model_params JSONB, -- 新增：设计师LLM模型参数
    designer_pattern_mode TEXT,
    master_pattern JSONB NOT NULL,
    game_config_dump JSONB
);

-- Game Players Table
CREATE TABLE game_players (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    game_id UUID NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_name_in_game TEXT NOT NULL,
    player_type TEXT NOT NULL, -- "Human" or "LLM"
    player_llm_model TEXT,
    player_llm_model_params JSONB, -- 新增：玩家LLM模型参数
    final_score INTEGER,
    final_guess JSONB,
    action_log JSONB, -- Array of strings
    queried_cells JSONB -- Array of {row, col}
);

-- Optional: Add indexes for frequently queried columns
CREATE INDEX idx_game_players_game_id ON game_players(game_id);
CREATE INDEX idx_games_created_at ON games(created_at DESC);
