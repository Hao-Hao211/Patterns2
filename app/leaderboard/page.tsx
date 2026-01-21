"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  ArrowLeft,
  Trophy,
  Medal,
  Award,
  Settings,
  Bot,
  RefreshCw,
  Target,
  Zap,
  History,
  DollarSign,
} from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { LLMModelParams } from "@/types/game-types"

interface LeaderboardEntry {
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
  avg_in_game_designer_score: number
  avg_meta_designer_score: number
  win_rate_as_player: number
  win_rate_as_designer: number
  inter_designer_win_rate: number
  inter_designer_wins: number
  total_games: number
  overall_win_rate: number
  overall_wins: number
  wins_as_player: number
  wins_as_designer: number
  cost_per_game: number
  total_cost: number
  total_input_tokens: number
  total_output_tokens: number
}

interface TestSet {
  id: string
  name: string
  description?: string
  status: string
  total_games: number
  completed_games: number
  created_at: string
}

export default function LeaderboardPage() {
  const searchParams = useSearchParams()
  const testSetId = searchParams.get("test_set_id")

  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([])
  const [testSets, setTestSets] = useState<TestSet[]>([])
  const [selectedTestSet, setSelectedTestSet] = useState<string>(testSetId || "all")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<
    | "elo"
    | "trueskill"
    | "player_avg"
    | "designer_avg"
    | "in_game_designer_avg"
    | "meta_designer_avg"
    | "player_winrate"
    | "designer_winrate"
    | "inter_designer_winrate"
    | "overall_winrate"
    | "cost_per_game"
  >("elo")

  useEffect(() => {
    fetchTestSets()
  }, [])

  useEffect(() => {
    fetchLeaderboard()
  }, [selectedTestSet])

  const fetchTestSets = async () => {
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets`)
      if (!response.ok) throw new Error("Failed to fetch test sets")
      const data = await response.json()
      setTestSets(data)
    } catch (err) {
      console.error("Error fetching test sets:", err)
    }
  }

  const fetchLeaderboard = async () => {
    try {
      setLoading(true)
      setError(null)

      const url =
        selectedTestSet === "all"
          ? `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/leaderboard`
          : `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/leaderboard?test_set_id=${selectedTestSet}`

      console.log("Fetching leaderboard from:", url)
      const response = await fetch(url)

      if (!response.ok) {
        const errorText = await response.text()
        throw new Error(`Failed to fetch leaderboard: ${response.status} ${errorText}`)
      }

      const data = await response.json()
      console.log("Leaderboard data:", data)
      console.log(
        "First entry overall data:",
        data.entries?.[0]
          ? {
              model_name: data.entries[0].model_name,
              wins_as_player: data.entries[0].wins_as_player,
              wins_as_designer: data.entries[0].wins_as_designer,
              games_as_player: data.entries[0].games_as_player,
              games_as_designer: data.entries[0].games_as_designer,
              overall_win_rate: data.entries[0].overall_win_rate,
              overall_wins: data.entries[0].overall_wins,
              total_games: data.entries[0].total_games,
              cost_per_game: data.entries[0].cost_per_game,
            }
          : "No entries",
      )
      console.log(
        "[v0] All entries with win data:",
        data.entries?.map((e: any) => ({
          model: e.model_name,
          wins_as_player: e.wins_as_player,
          wins_as_designer: e.wins_as_designer,
          games_as_player: e.games_as_player,
          games_as_designer: e.games_as_designer,
        })),
      )

      setLeaderboard(data.entries || [])
    } catch (err) {
      console.error("Error fetching leaderboard:", err)
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setLoading(false)
    }
  }

  const getSortedLeaderboard = () => {
    const sorted = [...leaderboard].sort((a, b) => {
      switch (sortBy) {
        case "elo":
          return b.elo_rating - a.elo_rating
        case "trueskill":
          return b.trueskill_rating - a.trueskill_rating
        case "player_avg":
          return b.avg_score_as_player - a.avg_score_as_player
        case "designer_avg":
          return b.avg_score_as_designer - a.avg_score_as_designer
        case "in_game_designer_avg":
          return (b.avg_in_game_designer_score || 0) - (a.avg_in_game_designer_score || 0)
        case "meta_designer_avg":
          return (b.avg_meta_designer_score || 0) - (a.avg_meta_designer_score || 0)
        case "player_winrate":
          return b.win_rate_as_player - a.win_rate_as_player
        case "designer_winrate":
          return b.win_rate_as_designer - a.win_rate_as_designer
        case "inter_designer_winrate":
          return (b.inter_designer_win_rate || 0) - (a.inter_designer_win_rate || 0)
        case "overall_winrate":
          return (b.overall_win_rate || 0) - (a.overall_win_rate || 0)
        case "cost_per_game":
          return (a.cost_per_game || 0) - (b.cost_per_game || 0)
        default:
          return b.elo_rating - a.elo_rating
      }
    })
    return sorted
  }

  const getRankIcon = (rank: number) => {
    switch (rank) {
      case 1:
        return <Trophy className="h-5 w-5 text-yellow-500" />
      case 2:
        return <Medal className="h-5 w-5 text-gray-400" />
      case 3:
        return <Award className="h-5 w-5 text-amber-600" />
      default:
        return <span className="text-slate-500 font-bold text-sm">#{rank}</span>
    }
  }

  const ModelParamsDialog = ({ entry }: { entry: LeaderboardEntry }) => {
    if (!entry.model_params || Object.keys(entry.model_params).length === 0) {
      return (
        <div className="flex items-center gap-2">
          <span className="font-medium">{entry.model_name}</span>
        </div>
      )
    }

    return (
      <Dialog>
        <DialogTrigger asChild>
          <Button variant="ghost" size="sm" className="h-auto p-0 font-medium hover:bg-transparent">
            <div className="flex items-center gap-2">
              <span>{entry.model_name}</span>
              <Settings className="h-3 w-3 text-slate-400" />
            </div>
          </Button>
        </DialogTrigger>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Bot className="h-4 w-4" />
              {entry.model_name} Configuration
            </DialogTitle>
            <DialogDescription>Model parameters used in this leaderboard</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {entry.model_params.temperature !== undefined && (
              <div className="flex justify-between items-center">
                <Label className="text-sm font-medium">Temperature:</Label>
                <Badge variant="outline">{entry.model_params.temperature}</Badge>
              </div>
            )}
            {entry.model_params.maxCompletionTokens !== undefined && (
              <div className="flex justify-between items-center">
                <Label className="text-sm font-medium">Max Completion Tokens:</Label>
                <Badge variant="outline">{entry.model_params.maxCompletionTokens}</Badge>
              </div>
            )}
            {entry.model_params.topP !== undefined && (
              <div className="flex justify-between items-center">
                <Label className="text-sm font-medium">Top P:</Label>
                <Badge variant="outline">{entry.model_params.topP}</Badge>
              </div>
            )}
            {entry.model_params.frequencyPenalty !== undefined && (
              <div className="flex justify-between items-center">
                <Label className="text-sm font-medium">Frequency Penalty:</Label>
                <Badge variant="outline">{entry.model_params.frequencyPenalty}</Badge>
              </div>
            )}
            {entry.model_params.presencePenalty !== undefined && (
              <div className="flex justify-between items-center">
                <Label className="text-sm font-medium">Presence Penalty:</Label>
                <Badge variant="outline">{entry.model_params.presencePenalty}</Badge>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  const CostDetailsDialog = ({ entry }: { entry: LeaderboardEntry }) => {
    return (
      <Dialog>
        <DialogTrigger asChild>
          <Button variant="ghost" size="sm" className="h-auto p-0 font-medium hover:bg-transparent">
            <div className="flex items-center gap-1">
              <DollarSign className="h-3 w-3 text-green-600" />
              <span className="font-mono">{entry.cost_per_game.toFixed(4)}</span>
            </div>
          </Button>
        </DialogTrigger>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <DollarSign className="h-4 w-4 text-green-600" />
              {entry.model_name} Cost Details
            </DialogTitle>
            <DialogDescription>Token usage and cost breakdown</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Cost per Game:</Label>
              <Badge variant="outline" className="font-mono">
                ${entry.cost_per_game.toFixed(4)}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Total Cost:</Label>
              <Badge variant="outline" className="font-mono">
                ${entry.total_cost.toFixed(4)}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Total Games:</Label>
              <Badge variant="outline">{entry.total_games}</Badge>
            </div>
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Input Tokens:</Label>
              <Badge variant="outline" className="font-mono">
                {entry.total_input_tokens.toLocaleString()}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Output Tokens:</Label>
              <Badge variant="outline" className="font-mono">
                {entry.total_output_tokens.toLocaleString()}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <Label className="text-sm font-medium">Avg Tokens/Game:</Label>
              <Badge variant="outline" className="font-mono">
                {Math.round(
                  (entry.total_input_tokens + entry.total_output_tokens) / entry.total_games,
                ).toLocaleString()}
              </Badge>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  // 构造游戏历史URL的函数
  const buildGameHistoryUrl = (modelName?: string) => {
    const params = new URLSearchParams()

    // 添加测试集过滤
    if (selectedTestSet !== "all") {
      params.set("test_set_id", selectedTestSet)
    }

    // 添加模型过滤
    if (modelName) {
      params.set("model", modelName)
    }

    const queryString = params.toString()
    return queryString ? `/history?${queryString}` : "/history"
  }

  // 获取通用游戏历史URL（不包含特定模型）
  const getGeneralGameHistoryUrl = () => {
    return buildGameHistoryUrl()
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-slate-600 mx-auto mb-4"></div>
          <p className="text-slate-600">Loading leaderboard...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-100 p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold text-slate-800 mb-2 flex items-center gap-3">
                <Trophy className="h-10 w-10 text-yellow-500" />
                Leaderboard
              </h1>
              <p className="text-slate-600">LLM performance rankings and statistics</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={fetchLeaderboard} disabled={loading}>
                <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                Refresh
              </Button>
              <Button variant="outline" asChild>
                <Link href={getGeneralGameHistoryUrl()}>
                  <History className="mr-2 h-4 w-4" />
                  View Game History
                </Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/">
                  <ArrowLeft className="mr-2 h-4 w-4" />
                  Back to Game
                </Link>
              </Button>
            </div>
          </div>
        </header>

        {error && (
          <Card className="mb-6 border-red-200 bg-red-50">
            <CardContent className="pt-6">
              <p className="text-red-600">Error: {error}</p>
              <Button onClick={fetchLeaderboard} className="mt-2" size="sm">
                Try Again
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Filters */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Filters & Sorting
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col sm:flex-row gap-4">
              <div className="flex-1 space-y-2">
                <Label>Test Set</Label>
                <Select value={selectedTestSet} onValueChange={setSelectedTestSet}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Games</SelectItem>
                    {testSets.map((testSet) => (
                      <SelectItem key={testSet.id} value={testSet.id}>
                        {testSet.name} ({testSet.completed_games}/{testSet.total_games} games)
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Sort By</Label>
                <Select value={sortBy} onValueChange={(value: any) => setSortBy(value)}>
                  <SelectTrigger className="w-48">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="elo">ELO Rating</SelectItem>
                    <SelectItem value="trueskill">TrueSkill Rating</SelectItem>
                    <SelectItem value="player_avg">Avg Player Score</SelectItem>
                    <SelectItem value="designer_avg">Avg Designer Score</SelectItem>
                    <SelectItem value="in_game_designer_avg">Average In-game Designer Score</SelectItem>
                    <SelectItem value="meta_designer_avg">Average Meta Designer Score</SelectItem>
                    <SelectItem value="player_winrate">Player Win Rate</SelectItem>
                    <SelectItem value="designer_winrate">Designer Win Rate</SelectItem>
                    <SelectItem value="inter_designer_winrate">Inter-Designer Win Rate</SelectItem>
                    <SelectItem value="overall_winrate">Overall Win Rate</SelectItem>
                    <SelectItem value="cost_per_game">Cost per Game</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        {leaderboard.length === 0 ? (
          <Card>
            <CardContent className="text-center py-12">
              <div className="text-slate-500 text-lg mb-4">No leaderboard data available</div>
              <div className="text-slate-400 mb-6">
                {selectedTestSet === "all"
                  ? "Play some games or run test sets to see rankings here"
                  : "This test set has no completed games yet"}
              </div>
              <div className="space-y-2">
                <p className="text-sm text-slate-500">Debug info:</p>
                <p className="text-xs text-slate-400">Selected test set: {selectedTestSet}</p>
                <p className="text-xs text-slate-400">Available test sets: {testSets.length}</p>
                <p className="text-xs text-slate-400">Loading: {loading.toString()}</p>
                <p className="text-xs text-slate-400">Error: {error || "none"}</p>
              </div>
              <Button asChild className="mt-4">
                <Link href="/test-sets">Setup Test Sets</Link>
              </Button>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <CardTitle>Rankings</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-16">Rank</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead className="text-center">ELO</TableHead>
                    <TableHead className="text-center">TrueSkill</TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Target className="h-3 w-3" />
                        Avg Player Score
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Zap className="h-3 w-3" />
                        Avg Designer Score
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Zap className="h-3 w-3" />
                        Average In-game Designer Score
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Zap className="h-3 w-3" />
                        Average Meta Designer Score
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Target className="h-3 w-3" />
                        Player Win Rate
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Zap className="h-3 w-3" />
                        Designer Win Rate
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Zap className="h-3 w-3" />
                        Inter-Designer Win Rate
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Trophy className="h-3 w-3" />
                        Overall Win Rate
                      </div>
                    </TableHead>
                    <TableHead className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <DollarSign className="h-3 w-3" />
                        Cost/Game
                      </div>
                    </TableHead>
                    <TableHead className="text-center">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {getSortedLeaderboard().map((entry, index) => {
                    const rank = index + 1
                    const overallWinRate = entry.overall_win_rate ?? 0
                    const overallWins = entry.overall_wins ?? 0
                    const totalGames = entry.total_games ?? 0
                    const costPerGame = entry.cost_per_game ?? 0
                    const interDesignerWinRate = entry.inter_designer_win_rate ?? 0
                    const interDesignerWins = entry.inter_designer_wins ?? 0

                    return (
                      <TableRow key={`${entry.model_name}-${JSON.stringify(entry.model_params)}`}>
                        <TableCell>
                          <div className="flex items-center justify-center">{getRankIcon(rank)}</div>
                        </TableCell>
                        <TableCell>
                          <div className="space-y-1">
                            <ModelParamsDialog entry={entry} />
                            <div className="text-xs text-slate-500">{totalGames} total games</div>
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <div className="font-bold text-blue-600">{entry.elo_rating.toFixed(1)}</div>
                        </TableCell>
                        <TableCell className="text-center">
                          <div className="font-bold text-purple-600">{entry.trueskill_rating.toFixed(1)}</div>
                          <div className="text-xs text-slate-500">
                            μ:{entry.trueskill_mu.toFixed(1)} σ:{entry.trueskill_sigma.toFixed(1)}
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <div className="font-mono">{entry.avg_score_as_player.toFixed(1)}</div>
                          <div className="text-xs text-slate-500">{entry.games_as_player} games</div>
                        </TableCell>
                        <TableCell className="text-center">
                          {entry.games_as_designer > 0 ? (
                            <>
                              <div className="font-mono">{entry.avg_score_as_designer.toFixed(1)}</div>
                              <div className="text-xs text-slate-500">{entry.games_as_designer} games</div>
                            </>
                          ) : (
                            <div className="text-slate-400 text-sm">-</div>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {entry.games_as_designer > 0 ? (
                            <>
                              <div className="font-mono font-semibold text-blue-600">
                                {(entry.avg_in_game_designer_score || 0).toFixed(1)}
                              </div>
                              <div className="text-xs text-slate-500">{entry.games_as_designer} games</div>
                            </>
                          ) : (
                            <div className="text-slate-400 text-sm">-</div>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {entry.games_as_designer > 0 ? (
                            <>
                              <div className="font-mono font-semibold text-purple-600">
                                {(entry.avg_meta_designer_score || 0).toFixed(1)}
                              </div>
                              <div className="text-xs text-slate-500">{entry.games_as_designer} games</div>
                            </>
                          ) : (
                            <div className="text-slate-400 text-sm">-</div>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          <Badge variant="secondary" className="font-mono">
                            {entry.win_rate_as_player.toFixed(1)}%
                          </Badge>
                          <div className="text-xs text-slate-500 mt-1">
                            {entry.wins_as_player ?? 0}/{entry.games_as_player} wins
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          {entry.games_as_designer > 0 ? (
                            <>
                              <Badge variant="secondary" className="font-mono">
                                {entry.win_rate_as_designer.toFixed(1)}%
                              </Badge>
                              <div className="text-xs text-slate-500 mt-1">
                                {entry.wins_as_designer ?? 0}/{entry.games_as_designer} wins
                              </div>
                            </>
                          ) : (
                            <div className="text-slate-400 text-sm">-</div>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {entry.games_as_designer > 0 ? (
                            <>
                              <Badge variant="secondary" className="font-mono">
                                {interDesignerWinRate.toFixed(1)}%
                              </Badge>
                              <div className="text-xs text-slate-500 mt-1">
                                {interDesignerWins}/{entry.games_as_designer} wins
                              </div>
                            </>
                          ) : (
                            <div className="text-slate-400 text-sm">-</div>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          <Badge variant={overallWinRate >= 50 ? "default" : "secondary"} className="font-mono">
                            {overallWinRate.toFixed(1)}%
                          </Badge>
                          <div className="text-xs text-slate-500 mt-1">
                            {overallWins}/{totalGames} wins
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <CostDetailsDialog entry={entry} />
                        </TableCell>
                        <TableCell className="text-center">
                          <Button variant="ghost" size="sm" asChild title={`View games for ${entry.model_name}`}>
                            <Link href={buildGameHistoryUrl(entry.model_name)}>
                              <History className="h-4 w-4" />
                            </Link>
                          </Button>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        {/* Legend */}
        <Card className="mt-8">
          <CardHeader>
            <CardTitle className="text-lg">Rating Systems Explained</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <h4 className="font-semibold text-blue-600 mb-2">ELO Rating</h4>
              <p className="text-sm text-slate-600">
                Traditional chess rating system adapted for multiplayer games. Higher values indicate better
                performance. Starting rating: 1500. Updates based on game outcomes relative to expected performance.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-purple-600 mb-2">TrueSkill Rating</h4>
              <p className="text-sm text-slate-600">
                Microsoft's Bayesian skill rating system. Shows conservative estimate (μ - 3σ) of true skill. Accounts
                for uncertainty in skill assessment. More accurate for players with fewer games.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-green-600 mb-2">Player Performance</h4>
              <p className="text-sm text-slate-600">
                Statistics when acting as a player trying to solve patterns. Win rate based on achieving highest score
                among all players in each game. Average score shows typical performance.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-orange-600 mb-2">Designer Performance</h4>
              <p className="text-sm text-slate-600">
                Statistics when acting as pattern designer. The old designer score (2×(max-min)) is shown for reference.
                The new scoring system uses two metrics: <strong>In-game Designer Score</strong> - a percentile-based
                score that can be directly compared with player scores for ranking, and{" "}
                <strong>Meta Designer Score</strong> - a normalized score (0-100) for cross-game comparison. These
                scores account for game difficulty, player skill distribution, and design competitiveness. Designer wins
                when their in-game score is the highest among all participants.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-indigo-600 mb-2">Inter-Designer Win Rate</h4>
              <p className="text-sm text-slate-600">
                Compares designers against each other within testing rounds. When multiple models act as designers in
                the same testing round, this metric shows how often a model's designer score is the highest among all
                designers in that round. This measures relative design quality when models are tested under similar
                conditions.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-amber-600 mb-2">Overall Win Rate</h4>
              <p className="text-sm text-slate-600">
                The percentage of individual games where the model achieved the highest score, regardless of role
                (player or designer). This metric provides the most comprehensive view of model performance by comparing
                all participants in each game directly. A model wins a game if its score (either as player or designer)
                is the highest among all participants in that specific game.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-green-600 mb-2">Cost per Game</h4>
              <p className="text-sm text-slate-600">
                Average cost in USD for each game based on token usage and model pricing. Calculated using official
                pricing from OpenAI and OpenRouter. Includes both input and output tokens for all actions during the
                game. Lower values indicate more cost-effective models. Click on the cost value to see detailed token
                usage breakdown.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
