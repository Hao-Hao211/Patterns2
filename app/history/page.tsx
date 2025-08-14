"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { GameBoard } from "@/components/game-board"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  ArrowLeft,
  Calendar,
  Grid3X3,
  Palette,
  User,
  Bot,
  Trophy,
  Eye,
  EyeOff,
  Search,
  Settings,
  X,
} from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import type { Grid, Symbol as AppSymbol, Position, LLMModelParams } from "@/types/game-types"

const ALL_SYMBOLS_DETAIL: AppSymbol[] = ["○", "△", "✖", "□", "★", "+"]

interface GamePlayerDetail {
  player_name_in_game: string
  player_type: "Human" | "LLM"
  player_llm_model: string | null
  player_llm_model_params: LLMModelParams | null
  final_score: number
  final_guess: Grid | null
  action_log: string[] | null
  queried_cells: Position[] | null
}

interface GameDetail {
  id: string
  created_at: string
  grid_size: number
  num_symbols: number
  designer_type: "Human" | "LLM"
  designer_llm_model: string | null
  designer_llm_model_params: LLMModelParams | null
  designer_pattern_mode: string | null
  master_pattern: Grid
  game_config_dump: any
  players: GamePlayerDetail[]
}

interface SearchFilters {
  modelName: string
  participantType: "all" | "designer" | "player"
}

// 默认参数配置
const getDefaultParams = (isDesigner = false): LLMModelParams => ({
  temperature: isDesigner ? 0.7 : 0.3, // 设计师使用更高的temperature增加创造性
  maxTokens: 2000,
  topP: 1.0,
  frequencyPenalty: 0,
  presencePenalty: 0,
})

// 合并用户参数和默认参数
const getMergedParams = (userParams: LLMModelParams | null, isDesigner = false): LLMModelParams => {
  const defaults = getDefaultParams(isDesigner)
  if (!userParams) return defaults

  return {
    temperature: userParams.temperature ?? defaults.temperature,
    maxTokens: userParams.maxTokens ?? defaults.maxTokens,
    topP: userParams.topP ?? defaults.topP,
    frequencyPenalty: userParams.frequencyPenalty ?? defaults.frequencyPenalty,
    presencePenalty: userParams.presencePenalty ?? defaults.presencePenalty,
  }
}

export default function HistoryPage() {
  const [games, setGames] = useState<GameDetail[]>([])
  const [filteredGames, setFilteredGames] = useState<GameDetail[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedGames, setExpandedGames] = useState<Set<string>>(new Set())

  // 搜索相关状态
  const [searchFilters, setSearchFilters] = useState<SearchFilters>({
    modelName: "",
    participantType: "all",
  })

  useEffect(() => {
    async function fetchGames() {
      setLoading(true)
      setError(null)
      try {
        const backendUrl = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/games`
        const response = await fetch(backendUrl)
        if (!response.ok) {
          const errData = await response.json()
          throw new Error(errData.error || errData.detail || "Failed to fetch games")
        }
        const gamesList = await response.json()

        // 获取每个游戏的详细信息
        const detailedGames = await Promise.all(
          gamesList.map(async (game: any) => {
            try {
              const detailResponse = await fetch(`${backendUrl}/${game.id}`)
              if (detailResponse.ok) {
                return await detailResponse.json()
              }
              return null
            } catch (err) {
              console.error(`Failed to fetch details for game ${game.id}:`, err)
              return null
            }
          }),
        )

        const validGames = detailedGames.filter((game) => game !== null)
        setGames(validGames)
        setFilteredGames(validGames)
      } catch (err) {
        setError(err instanceof Error ? err.message : "An unknown error occurred")
      } finally {
        setLoading(false)
      }
    }
    fetchGames()
  }, [])

  // 搜索过滤逻辑
  useEffect(() => {
    if (!searchFilters.modelName.trim()) {
      setFilteredGames(games)
      return
    }

    const filtered = games.filter((game) => {
      const modelNameLower = searchFilters.modelName.toLowerCase()

      // 检查设计师模型
      const designerMatches =
        searchFilters.participantType === "all" || searchFilters.participantType === "designer"
          ? game.designer_llm_model?.toLowerCase().includes(modelNameLower)
          : false

      // 检查玩家模型
      const playerMatches =
        searchFilters.participantType === "all" || searchFilters.participantType === "player"
          ? game.players.some((player) => player.player_llm_model?.toLowerCase().includes(modelNameLower))
          : false

      return designerMatches || playerMatches
    })

    setFilteredGames(filtered)
  }, [games, searchFilters])

  const toggleGameExpansion = (gameId: string) => {
    setExpandedGames((prev) => {
      const newSet = new Set(prev)
      if (newSet.has(gameId)) {
        newSet.delete(gameId)
      } else {
        newSet.add(gameId)
      }
      return newSet
    })
  }

  const calculateDesignerScore = (players: GamePlayerDetail[]) => {
    if (players.length < 2) return 0
    const scores = players.map((p) => p.final_score).filter((s) => s !== null && s !== undefined)
    if (scores.length < 2) return 0
    const maxScore = Math.max(...scores)
    const minScore = Math.min(...scores)
    return 2 * (maxScore - minScore)
  }

  const clearSearch = () => {
    setSearchFilters({
      modelName: "",
      participantType: "all",
    })
  }

  // 模型参数显示组件 - 修改为总是显示可点击Badge
  const ModelParamsDialog = ({
    modelName,
    params,
    triggerText,
    isDesigner = false,
  }: {
    modelName: string
    params: LLMModelParams | null
    triggerText: string
    isDesigner?: boolean
  }) => {
    const mergedParams = getMergedParams(params, isDesigner)
    const userParams = params || {}

    return (
      <Dialog>
        <DialogTrigger asChild>
          <Button variant="ghost" size="sm" className="h-auto p-1">
            <Badge variant="secondary" className="cursor-pointer hover:bg-slate-300 flex items-center gap-1">
              {triggerText}
              <Settings className="h-3 w-3" />
            </Badge>
          </Button>
        </DialogTrigger>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Settings className="h-4 w-4" />
              {modelName} Parameters
            </DialogTitle>
            <DialogDescription>Complete model configuration (custom + defaults)</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Temperature:</Label>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{mergedParams.temperature}</Badge>
                {userParams.temperature === undefined && (
                  <Badge variant="secondary" className="text-xs">
                    default
                  </Badge>
                )}
              </div>
            </div>

            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Max Tokens:</Label>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{mergedParams.maxTokens}</Badge>
                {userParams.maxTokens === undefined && (
                  <Badge variant="secondary" className="text-xs">
                    default
                  </Badge>
                )}
              </div>
            </div>

            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Top P:</Label>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{mergedParams.topP}</Badge>
                {userParams.topP === undefined && (
                  <Badge variant="secondary" className="text-xs">
                    default
                  </Badge>
                )}
              </div>
            </div>

            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Frequency Penalty:</Label>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{mergedParams.frequencyPenalty}</Badge>
                {userParams.frequencyPenalty === undefined && (
                  <Badge variant="secondary" className="text-xs">
                    default
                  </Badge>
                )}
              </div>
            </div>

            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Presence Penalty:</Label>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{mergedParams.presencePenalty}</Badge>
                {userParams.presencePenalty === undefined && (
                  <Badge variant="secondary" className="text-xs">
                    default
                  </Badge>
                )}
              </div>
            </div>

            <Separator />

            <div className="text-xs text-slate-600 space-y-1">
              <p>
                <strong>Custom Parameters:</strong>{" "}
                {Object.keys(userParams).length > 0 ? Object.keys(userParams).join(", ") : "None"}
              </p>
              <p>
                <strong>Note:</strong> Values marked as "default" use system defaults
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-slate-600 mx-auto mb-4"></div>
          <p className="text-slate-600">Loading game history...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="text-red-600">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-red-500 mb-4">{error}</p>
            <Button variant="outline" asChild>
              <Link href="/">Back to Game</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-100 p-4 sm:p-6 lg:p-8">
      <div className="max-w-6xl mx-auto">
        <header className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold text-slate-800 mb-2">Game History</h1>
              <p className="text-slate-600">Complete records of all past games</p>
            </div>
            <Button variant="outline" asChild>
              <Link href="/">
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to Game
              </Link>
            </Button>
          </div>
        </header>

        {/* 搜索过滤器 */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Search className="h-5 w-5" />
              Search & Filter Games
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col sm:flex-row gap-4 items-end">
              <div className="flex-1 space-y-2">
                <Label htmlFor="modelSearch">Model Name</Label>
                <Input
                  id="modelSearch"
                  placeholder="e.g., gpt-4o, chatgpt-4o-latest..."
                  value={searchFilters.modelName}
                  onChange={(e) => setSearchFilters((prev) => ({ ...prev, modelName: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="participantType">Participant Type</Label>
                <Select
                  value={searchFilters.participantType}
                  onValueChange={(value: "all" | "designer" | "player") =>
                    setSearchFilters((prev) => ({ ...prev, participantType: value }))
                  }
                >
                  <SelectTrigger className="w-40">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All</SelectItem>
                    <SelectItem value="designer">Designer Only</SelectItem>
                    <SelectItem value="player">Player Only</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {(searchFilters.modelName || searchFilters.participantType !== "all") && (
                <Button variant="outline" onClick={clearSearch} size="sm">
                  <X className="h-4 w-4 mr-1" />
                  Clear
                </Button>
              )}
            </div>
            {searchFilters.modelName && (
              <div className="mt-3 text-sm text-slate-600">
                Found {filteredGames.length} game{filteredGames.length !== 1 ? "s" : ""} matching "
                {searchFilters.modelName}"
                {searchFilters.participantType !== "all" && ` (${searchFilters.participantType} only)`}
              </div>
            )}
          </CardContent>
        </Card>

        {filteredGames.length === 0 ? (
          <Card>
            <CardContent className="text-center py-12">
              {searchFilters.modelName ? (
                <>
                  <div className="text-slate-500 text-lg mb-4">No games found matching your search</div>
                  <div className="text-slate-400 mb-6">Try adjusting your search criteria</div>
                  <Button onClick={clearSearch} variant="outline">
                    Clear Search
                  </Button>
                </>
              ) : (
                <>
                  <div className="text-slate-500 text-lg mb-4">No game history found</div>
                  <div className="text-slate-400 mb-6">Play some games to see them here!</div>
                  <Button asChild>
                    <Link href="/">Start Playing</Link>
                  </Button>
                </>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-6">
            {filteredGames.map((game) => {
              const isExpanded = expandedGames.has(game.id)
              const designerScore = calculateDesignerScore(game.players)
              const symbolsInUse = ALL_SYMBOLS_DETAIL.slice(0, game.num_symbols)

              return (
                <Card key={game.id} className="overflow-hidden">
                  <CardHeader className="pb-4">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <CardTitle className="flex items-center gap-3 mb-2">
                          <Calendar className="h-5 w-5 text-slate-500" />
                          {new Date(game.created_at).toLocaleString()}
                          <Badge variant="outline" className="ml-2">
                            Game #{game.id.slice(-8)}
                          </Badge>
                        </CardTitle>
                        <div className="flex flex-wrap gap-4 text-sm text-slate-600">
                          <div className="flex items-center gap-1">
                            <Grid3X3 className="h-4 w-4" />
                            {game.grid_size}×{game.grid_size} Grid
                          </div>
                          <div className="flex items-center gap-1">
                            <Palette className="h-4 w-4" />
                            {game.num_symbols} Symbols
                          </div>
                          <div className="flex items-center gap-1">
                            {game.designer_type === "Human" ? (
                              <User className="h-4 w-4" />
                            ) : (
                              <Bot className="h-4 w-4" />
                            )}
                            Designer: {game.designer_type}
                            {game.designer_llm_model && (
                              <ModelParamsDialog
                                modelName={game.designer_llm_model}
                                params={game.designer_llm_model_params}
                                triggerText={game.designer_llm_model}
                                isDesigner={true}
                              />
                            )}
                          </div>
                          {game.designer_pattern_mode && (
                            <Badge variant="outline" className="text-xs">
                              {game.designer_pattern_mode}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <Button variant="ghost" size="sm" onClick={() => toggleGameExpansion(game.id)} className="ml-4">
                        {isExpanded ? (
                          <>
                            <EyeOff className="mr-2 h-4 w-4" />
                            Hide Details
                          </>
                        ) : (
                          <>
                            <Eye className="mr-2 h-4 w-4" />
                            View Details
                          </>
                        )}
                      </Button>
                    </div>
                  </CardHeader>

                  {isExpanded && (
                    <CardContent className="pt-0">
                      <div className="grid gap-6">
                        {/* Designer Section */}
                        <div>
                          <h3 className="text-lg font-semibold mb-3 flex items-center gap-2">
                            <Trophy className="h-5 w-5 text-yellow-500" />
                            Designer Results
                          </h3>
                          <div className="bg-slate-50 rounded-lg p-4">
                            <div className="flex items-center justify-between mb-4">
                              <div>
                                <div className="font-medium flex items-center gap-2">
                                  {game.designer_type} Designer
                                  {game.designer_llm_model && (
                                    <ModelParamsDialog
                                      modelName={game.designer_llm_model}
                                      params={game.designer_llm_model_params}
                                      triggerText={game.designer_llm_model}
                                      isDesigner={true}
                                    />
                                  )}
                                </div>
                                <div className="text-sm text-slate-600">
                                  Pattern Mode: {game.designer_pattern_mode || "Unknown"}
                                </div>
                              </div>
                              <div className="text-right">
                                <div className="text-2xl font-bold text-blue-600">{designerScore}</div>
                                <div className="text-xs text-slate-500">Designer Score</div>
                              </div>
                            </div>
                            <div>
                              <div className="text-sm font-medium mb-2">Master Pattern:</div>
                              <div className="flex justify-center">
                                <GameBoard
                                  grid={game.master_pattern}
                                  onCellClick={() => {}}
                                  selectedCells={[]}
                                  queriedCells={[]}
                                  isGuessing={false}
                                  finalGuess={null}
                                  masterPattern={game.master_pattern}
                                  isGameOver={true}
                                  gridSize={game.grid_size}
                                  symbolsInUse={symbolsInUse}
                                  readOnly={true}
                                />
                              </div>
                            </div>
                          </div>
                        </div>

                        <Separator />

                        {/* Players Section */}
                        <div>
                          <h3 className="text-lg font-semibold mb-3">Player Results</h3>
                          <div className="grid gap-4">
                            {game.players
                              .sort((a, b) => b.final_score - a.final_score)
                              .map((player, index) => (
                                <Card key={index} className="bg-slate-50">
                                  <CardHeader className="pb-3">
                                    <div className="flex items-center justify-between">
                                      <CardTitle className="text-base flex items-center gap-2">
                                        {player.player_type === "Human" ? (
                                          <User className="h-4 w-4" />
                                        ) : (
                                          <Bot className="h-4 w-4" />
                                        )}
                                        {player.player_name_in_game}
                                        {player.player_llm_model && (
                                          <ModelParamsDialog
                                            modelName={player.player_llm_model}
                                            params={player.player_llm_model_params}
                                            triggerText={player.player_llm_model}
                                            isDesigner={false}
                                          />
                                        )}
                                        {index === 0 && <Badge className="bg-yellow-500 text-white">Winner</Badge>}
                                      </CardTitle>
                                      <div className="text-right">
                                        <div className="text-xl font-bold text-green-600">{player.final_score}</div>
                                        <div className="text-xs text-slate-500">Final Score</div>
                                      </div>
                                    </div>
                                  </CardHeader>
                                  <CardContent className="pt-0">
                                    <div className="grid md:grid-cols-2 gap-4">
                                      {/* Player's Final Guess */}
                                      <div>
                                        <div className="text-sm font-medium mb-2">Final Guess:</div>
                                        {player.final_guess ? (
                                          <div className="flex justify-center">
                                            <GameBoard
                                              grid={player.final_guess}
                                              onCellClick={() => {}}
                                              selectedCells={[]}
                                              queriedCells={player.queried_cells || []}
                                              isGuessing={false}
                                              finalGuess={player.final_guess}
                                              masterPattern={game.master_pattern}
                                              isGameOver={true}
                                              gridSize={game.grid_size}
                                              symbolsInUse={symbolsInUse}
                                              readOnly={true}
                                            />
                                          </div>
                                        ) : (
                                          <div className="text-slate-500 text-center py-4">No guess submitted</div>
                                        )}
                                      </div>

                                      {/* Game Log */}
                                      <div>
                                        <div className="text-sm font-medium mb-2">Game Log:</div>
                                        <ScrollArea className="h-48 w-full border rounded-md p-3 bg-white">
                                          {player.action_log && player.action_log.length > 0 ? (
                                            <div className="space-y-1">
                                              {player.action_log.map((log, logIndex) => (
                                                <div
                                                  key={logIndex}
                                                  className="text-xs text-slate-700 border-b border-slate-100 pb-1"
                                                >
                                                  {log}
                                                </div>
                                              ))}
                                            </div>
                                          ) : (
                                            <div className="text-slate-500 text-xs">No actions recorded</div>
                                          )}
                                        </ScrollArea>
                                      </div>
                                    </div>

                                    {/* Queried Cells Info */}
                                    {player.queried_cells && player.queried_cells.length > 0 && (
                                      <div className="mt-4 pt-3 border-t">
                                        <div className="text-sm font-medium mb-1">Observed Cells:</div>
                                        <div className="flex flex-wrap gap-1">
                                          {player.queried_cells.map((cell, cellIndex) => (
                                            <Badge key={cellIndex} variant="outline" className="text-xs">
                                              {String.fromCharCode(65 + cell.col)}
                                              {cell.row + 1}
                                            </Badge>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                  </CardContent>
                                </Card>
                              ))}
                          </div>
                        </div>
                      </div>
                    </CardContent>
                  )}
                </Card>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
