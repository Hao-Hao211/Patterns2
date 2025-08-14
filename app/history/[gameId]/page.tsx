"use client"

import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { GameBoard } from "@/components/game-board"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { ArrowLeft, Bot, User, Trophy, Calendar, Grid3X3, Palette } from "lucide-react"
import type { Grid, Symbol as AppSymbol, Position } from "@/types/game-types"

const ALL_SYMBOLS_DETAIL: AppSymbol[] = ["○", "△", "✖", "□", "★", "+"]

interface GamePlayerDetail {
  player_name_in_game: string
  player_type: "Human" | "LLM"
  player_llm_model: string | null
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
  designer_pattern_mode: string | null
  master_pattern: Grid
  game_config_dump: any
  players: GamePlayerDetail[]
}

export default function GameDetailPage() {
  const params = useParams()
  const gameId = params.gameId as string

  const [game, setGame] = useState<GameDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (gameId) {
      async function fetchGameDetails() {
        setLoading(true)
        setError(null)
        try {
          const backendUrl = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/games/${gameId}`
          const response = await fetch(backendUrl)
          if (!response.ok) {
            const errData = await response.json()
            throw new Error(errData.error || errData.detail || "Failed to fetch game details")
          }
          const data = await response.json()
          setGame(data)
        } catch (err) {
          setError(err instanceof Error ? err.message : "An unknown error occurred")
        } finally {
          setLoading(false)
        }
      }
      fetchGameDetails()
    }
  }, [gameId])

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-slate-600 mx-auto mb-4"></div>
          <p className="text-slate-600">Loading game details...</p>
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
              <Link href="/history">Back to History</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!game) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Game Not Found</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-slate-600 mb-4">The requested game could not be found.</p>
            <Button variant="outline" asChild>
              <Link href="/history">Back to History</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const symbolsInUse = ALL_SYMBOLS_DETAIL.slice(0, game.num_symbols)
  const designerScore = (() => {
    if (game.players.length < 2) return 0
    const scores = game.players.map((p) => p.final_score).filter((s) => s !== null && s !== undefined)
    if (scores.length < 2) return 0
    const maxScore = Math.max(...scores)
    const minScore = Math.min(...scores)
    return 2 * (maxScore - minScore)
  })()

  return (
    <div className="min-h-screen bg-slate-100 p-4 sm:p-6 lg:p-8">
      <div className="max-w-6xl mx-auto">
        <header className="mb-8">
          <Button variant="ghost" asChild className="mb-4">
            <Link href="/history">
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to History
            </Link>
          </Button>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-3">
                <Calendar className="h-6 w-6 text-slate-500" />
                Game Details
                <Badge variant="outline">#{game.id.slice(-8)}</Badge>
              </CardTitle>
              <CardDescription>Played on {new Date(game.created_at).toLocaleString()}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-6 text-sm">
                <div className="flex items-center gap-2">
                  <Grid3X3 className="h-4 w-4 text-slate-500" />
                  <span className="font-medium">Grid Size:</span>
                  <span>
                    {game.grid_size}×{game.grid_size}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Palette className="h-4 w-4 text-slate-500" />
                  <span className="font-medium">Symbols:</span>
                  <span>{game.num_symbols}</span>
                </div>
                <div className="flex items-center gap-2">
                  {game.designer_type === "Human" ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                  <span className="font-medium">Designer:</span>
                  <span>{game.designer_type}</span>
                  {game.designer_llm_model && <Badge variant="secondary">{game.designer_llm_model}</Badge>}
                </div>
                {game.designer_pattern_mode && (
                  <div className="flex items-center gap-2">
                    <span className="font-medium">Pattern Mode:</span>
                    <Badge variant="outline">{game.designer_pattern_mode}</Badge>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </header>

        <div className="grid gap-8">
          {/* Designer Section */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Trophy className="h-5 w-5 text-yellow-500" />
                Designer Results
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid md:grid-cols-2 gap-6">
                <div>
                  <div className="bg-slate-50 rounded-lg p-4 mb-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium">
                          {game.designer_type} Designer
                          {game.designer_llm_model && (
                            <Badge variant="secondary" className="ml-2">
                              {game.designer_llm_model}
                            </Badge>
                          )}
                        </p>
                        <p className="text-sm text-slate-600">
                          Pattern Mode: {game.designer_pattern_mode || "Unknown"}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-3xl font-bold text-blue-600">{designerScore}</p>
                        <p className="text-sm text-slate-500">Designer Score</p>
                      </div>
                    </div>
                  </div>
                </div>
                <div>
                  <div className="font-medium mb-3">Master Pattern (Solution):</div>
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
            </CardContent>
          </Card>

          <Separator />

          {/* Players Section */}
          <div>
            <h2 className="text-2xl font-bold mb-6">Player Results</h2>
            <div className="grid gap-6">
              {game.players
                .sort((a, b) => b.final_score - a.final_score)
                .map((player, index) => (
                  <Card key={index}>
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          {player.player_type === "Human" ? <User className="h-5 w-5" /> : <Bot className="h-5 w-5" />}
                          {player.player_name_in_game}
                          {player.player_llm_model && <Badge variant="secondary">{player.player_llm_model}</Badge>}
                          {index === 0 && <Badge className="bg-yellow-500 text-white">🏆 Winner</Badge>}
                        </div>
                        <div className="text-right">
                          <p className="text-2xl font-bold text-green-600">{player.final_score}</p>
                          <p className="text-sm text-slate-500">Final Score</p>
                        </div>
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="grid lg:grid-cols-2 gap-6">
                        {/* Player's Final Guess */}
                        <div>
                          <h4 className="font-medium mb-3">Final Guess:</h4>
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
                            <div className="text-center py-8 text-slate-500">No guess submitted</div>
                          )}

                          {/* Queried Cells Info */}
                          {player.queried_cells && player.queried_cells.length > 0 && (
                            <div className="mt-4">
                              <div className="text-sm font-medium mb-2">Observed Cells:</div>
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
                        </div>

                        {/* Game Log */}
                        <div>
                          <h4 className="font-medium mb-3">Complete Game Log:</h4>
                          <ScrollArea className="h-64 w-full border rounded-md p-4 bg-white">
                            {player.action_log && player.action_log.length > 0 ? (
                              <div className="space-y-2">
                                {player.action_log.map((log, logIndex) => (
                                  <div key={logIndex} className="text-sm text-slate-700 border-b border-slate-100 pb-2">
                                    <span className="text-xs text-slate-500 mr-2">#{logIndex + 1}</span>
                                    {log}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <div className="text-slate-500 text-center py-8">No actions recorded</div>
                            )}
                          </ScrollArea>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
