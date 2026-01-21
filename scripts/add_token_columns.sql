-- Add token usage columns to game_players table
ALTER TABLE game_players ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0;
ALTER TABLE game_players ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_game_players_tokens ON game_players(input_tokens, output_tokens);
