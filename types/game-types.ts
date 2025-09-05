// Create this new file to hold shared types

export type Symbol = "+" | "○" | "△" | "□" | "★" | "✖"
export type Cell = Symbol | "?" | null
export type Grid = Cell[][]
export type Position = { row: number; col: number }

// 新增：LLM模型参数配置
export interface LLMModelParams {
  temperature?: number
  maxCompletionTokens?: number // 改为 maxCompletionTokens
  topP?: number
  frequencyPenalty?: number
  presencePenalty?: number
}

// 扩展玩家配置以包含模型参数
export interface PlayerConfigWithParams {
  id: string
  name: string
  type: "Human" | "LLM"
  llmModel?: string
  llmModelParams?: LLMModelParams
  finalScore?: number
}
