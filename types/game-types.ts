export type Symbol = "+" | "○" | "△" | "□" | "★" | "✖"
export type Cell = Symbol | null
export type Grid = Cell[][]

export interface Position {
  row: number
  col: number
}

export interface LLMModelParams {
  temperature?: number
  maxCompletionTokens?: number
  topP?: number
  frequencyPenalty?: number
  presencePenalty?: number
  reasoningEffort?: string // xhigh/high/medium/low/minimal/none
  chatHistoryEnabled?: boolean // Multi-turn chat history, default false (single-turn)
}

export interface PlayerState {
  id: string
  name: string
  type: "Human" | "LLM"
  llmModel?: string
  llmModelParams?: LLMModelParams
  grid: string[][]
  queriedCells: Position[]
  selectedCells: Position[]
  isGuessing: boolean
  isGuessMode: boolean
  log: string[]
  score: number | null
  isFinished: boolean
  finalGuess: string[][] | null
  turnNumber: number
  isWaitingForLLM: boolean
  isPaused: boolean
  inputTokens?: number
  outputTokens?: number
}

export interface GameState {
  gameId: string
  gridSize: number
  numSymbols: number
  symbolsInUse: Symbol[]
  masterPattern: Grid
  currentPhase: "setup" | "playing" | "results"
  playerStates: Record<string, PlayerState>
  allPlayersFinished: boolean
  currentTurn: number
  designerType: "Human" | "LLM"
  designerLLMModel?: string
  designerLLMModelParams?: LLMModelParams
  designerPatternMode?: string
  designerPrompt?: string
}

export interface GameConfig {
  baseSettings: {
    gridSize: number
    numSymbols: number
  }
  designer: {
    type: "Human" | "LLM"
    llmModel?: string
    llmModelParams?: LLMModelParams
    patternMode?: string
    customPattern?: Grid
    llmPrompt?: string
  }
  players: Array<{
    id: string
    name: string
    type: "Human" | "LLM"
    llmModel?: string
    llmModelParams?: LLMModelParams
  }>
}

export interface LeaderboardEntry {
  model_name: string
  model_params?: LLMModelParams
  elo_rating: number
  trueskill_rating: number
  trueskill_mu: number
  trueskill_sigma: number
  games_as_player: number
  games_as_designer: number
  avg_score_as_player: number
  avg_score_as_designer: number
  win_rate_as_player: number
  win_rate_as_designer: number
  total_games: number
  overall_win_rate: number
  overall_wins: number
  overall_designer_win_rate: number
  overall_designer_wins: number
  cost_per_game: number
  total_cost: number
  total_input_tokens: number
  total_output_tokens: number
  total_score_as_player: number
  theoretical_max_scientist_score: number
  total_score_as_designer: number
}

export interface TestSet {
  id: string
  name: string
  description?: string
  status: string
  total_games: number
  completed_games: number
  created_at: string
  config?: any
}

export interface EvolvingConfig {
  enabled: boolean
  mode: 'fresh' | 'imported'
  import_game_ids?: string[]
  accumulate: boolean
}

export interface TestSetParticipant {
  participant_type: 'Human' | 'LLM'
  human_name?: string  // required for Human participants (unique nickname)
  model_name?: string  // optional for Human participants
  model_params?: LLMModelParams
  evolving_config?: EvolvingConfig
}

export interface TestSetGameConfig {
  grid_size: number
  num_symbols: number
  optional_prompt?: string
  custom_pattern?: string[][]
  repeat_count: number
}

export interface TestSetConfig {
  name: string
  description?: string
  participants: TestSetParticipant[]
  llm_rotate_designer: boolean
  games: TestSetGameConfig[]
}
