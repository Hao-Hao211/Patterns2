"use client"

import { useState, useEffect, useRef } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { GameBoard } from "@/components/game-board"
import { GameLog } from "@/components/game-log"
import { ArrowLeft, Trophy, Clock, Users, CheckCircle, RefreshCw, Eye, Brain } from "lucide-react"
import type { Grid, Symbol } from "@/types/game-types"

const ALL_SYMBOLS: Symbol[] = ["○", "△", "✖", "□", "★", "+"]

interface TestSetConfig {
  participants: Array<{
    model_name: string
    model_params?: any
  }>
  llm_rotate_designer: boolean
  games: Array<{
    grid_size: number
    num_symbols: number
    optional_prompt?: string
    custom_pattern?: string[][]
    repeat_count: number
  }>
}

interface TestSet {
  id: string
  name: string
  description?: string
  config: TestSetConfig
  status: string
  total_games: number
  completed_games: number
  created_at: string
}

interface PlayerState {
  id: string
  name: string
  type: string
  llmModel?: string
  llmModelParams?: any
  grid: Grid
  queriedCells: Array<{ row: number; col: number }>
  selectedCells: Array<{ row: number; col: number }>
  isGuessing: boolean
  isGuessMode: boolean
  log: string[]
  score: number | null
  isFinished: boolean
  finalGuess: Grid | null
  turnNumber: number
  isWaitingForLLM: boolean
  isPaused: boolean
}

interface GameState {
  gameId: string
  gameIndex: number
  gameConfig: any
  masterPattern: Grid
  currentPhase: "playing" | "results"
  playerStates: Record<string, PlayerState>
  allPlayersFinished: boolean
  currentTurn: number
}

export default function TestSetExecutePage() {
  const params = useParams()
  const router = useRouter()
  const testSetId = params.testSetId as string

  const [testSet, setTestSet] = useState<TestSet | null>(null)
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<string>("")

  // Use ref to track user selection to prevent re-renders from affecting it
  const userSelectedTabRef = useRef(false)
  const lastGameIdRef = useRef<string>("")

  useEffect(() => {
    fetchTestSet()
    fetchCurrentGame()

    // 设置定时刷新以显示实时进度和游戏状态
    const interval = setInterval(() => {
      fetchTestSet()
      fetchCurrentGame()
    }, 2000) // 每2秒刷新一次

    return () => clearInterval(interval)
  }, [testSetId])

  const fetchTestSet = async () => {
    try {
      if (loading) {
        setError(null)
      }

      const response = await fetch(`http://127.0.0.1:8000/api/test-sets/${testSetId}`)
      if (!response.ok) throw new Error("Failed to fetch test set")

      const testSetData = await response.json()
      console.log("Test set data:", testSetData)

      // Log custom patterns in the test set config
      if (testSetData.config && testSetData.config.games) {
        testSetData.config.games.forEach((game: any, index: number) => {
          if (game.custom_pattern) {
            console.log(`Test set game ${index + 1} has custom pattern:`, game.custom_pattern)
          }
        })
      }

      setTestSet(testSetData)
    } catch (err) {
      if (loading) {
        setError(err instanceof Error ? err.message : "Unknown error")
      }
    } finally {
      setLoading(false)
    }
  }

  const fetchCurrentGame = async () => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/test-sets/${testSetId}/current-game`)
      if (response.ok) {
        const gameData = await response.json()
        if (gameData.gameId) {
          // Debug log to check if custom pattern is being used
          console.log("Current game data:", gameData)
          if (gameData.gameConfig && gameData.masterPattern) {
            console.log("Master pattern in current game:", gameData.masterPattern)
            console.log("Game config:", gameData.gameConfig)
          }

          setGameState(gameData)

          // Check if this is a new game
          const isNewGame = lastGameIdRef.current !== gameData.gameId
          if (isNewGame) {
            lastGameIdRef.current = gameData.gameId
            // Reset user selection flag for new games
            userSelectedTabRef.current = false
            console.log("New game detected:", gameData.gameId)
          }

          // Only auto-select tab if:
          // 1. User hasn't manually selected a tab, AND
          // 2. No active tab is set, OR the current active tab doesn't exist in current game
          const availablePlayerIds = Object.keys(gameData.playerStates || {})
          const currentTabExists = availablePlayerIds.includes(activeTab)

          if (!userSelectedTabRef.current && (!activeTab || !currentTabExists)) {
            if (availablePlayerIds.length > 0) {
              setActiveTab(availablePlayerIds[0])
            }
          }
        } else {
          setGameState(null)
          // Reset when no game is active
          if (lastGameIdRef.current) {
            lastGameIdRef.current = ""
            userSelectedTabRef.current = false
            setActiveTab("")
          }
        }
      }
    } catch (err) {
      // 静默处理游戏状态获取错误
      console.error("Failed to fetch current game:", err)
    }
  }

  const handleStartTestSet = async () => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/test-sets/${testSetId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      })

      if (response.ok) {
        await fetchTestSet()
      } else {
        throw new Error("Failed to start test set")
      }
    } catch (err) {
      console.error("Failed to start test set:", err)
      setError(err instanceof Error ? err.message : "Failed to start test set")
    }
  }

  const handleTabChange = (value: string) => {
    console.log("User manually selected tab:", value)
    setActiveTab(value)
    userSelectedTabRef.current = true
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case "running":
        return "default"
      case "completed":
        return "secondary"
      case "failed":
        return "destructive"
      default:
        return "secondary"
    }
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "running":
        return <RefreshCw className="h-4 w-4 animate-spin" />
      case "completed":
        return <CheckCircle className="h-4 w-4" />
      case "failed":
        return <Trophy className="h-4 w-4" />
      default:
        return <Trophy className="h-4 w-4" />
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-slate-600 mx-auto mb-4"></div>
          <p className="text-slate-600">Loading test set...</p>
        </div>
      </div>
    )
  }

  if (error || !testSet) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="text-red-600">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-red-500 mb-4">{error || "Test set not found"}</p>
            <Button variant="outline" asChild>
              <Link href="/test-sets">Back to Test Sets</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-100">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/test-sets">
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to Test Sets
              </Link>
            </Button>
            <Separator orientation="vertical" className="h-6" />
            <div>
              <h1 className="text-xl font-semibold">{testSet.name}</h1>
              <p className="text-sm text-slate-600">
                Test Set Execution{" "}
                <Badge variant={getStatusColor(testSet.status)} className="ml-2">
                  {getStatusIcon(testSet.status)}
                  {testSet.status}
                </Badge>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-sm font-medium">
                Progress: {testSet.completed_games}/{testSet.total_games}
              </div>
              <Progress value={(testSet.completed_games / testSet.total_games) * 100} className="w-32 h-2" />
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                fetchTestSet()
                fetchCurrentGame()
              }}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="p-4">
        {gameState ? (
          // 显示当前游戏状态
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Eye className="h-5 w-5" />
                  Live Game View - Game {gameState.gameIndex + 1}/{testSet.total_games}
                  {gameState.currentPhase === "playing" && (
                    <Badge variant="default" className="ml-2">
                      <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                      Turn {gameState.currentTurn}
                    </Badge>
                  )}
                  {gameState.currentPhase === "results" && (
                    <Badge variant="secondary" className="ml-2">
                      <CheckCircle className="h-3 w-3 mr-1" />
                      Completed
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
            </Card>

            {/* 游戏界面 */}
            <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
              <TabsList
                className="grid w-full gap-1"
                style={{ gridTemplateColumns: `repeat(${Object.keys(gameState.playerStates).length}, minmax(0, 1fr))` }}
              >
                {Object.values(gameState.playerStates).map((player) => {
                  let triggerClass = "data-[state=active]:bg-slate-200"
                  let statusText = ""
                  let statusIcon = null

                  if (player.isFinished) {
                    triggerClass = "bg-green-200 text-green-800 data-[state=active]:bg-green-300"
                    statusText = "(Done)"
                    statusIcon = <CheckCircle className="h-4 w-4 ml-1" />
                  } else if (player.isWaitingForLLM) {
                    triggerClass = "bg-orange-200 text-orange-800 data-[state=active]:bg-orange-300 animate-pulse"
                    statusText = "(AI Thinking...)"
                    statusIcon = <Brain className="h-4 w-4 ml-1 animate-spin" />
                  } else {
                    triggerClass = "bg-yellow-200 text-yellow-800 data-[state=active]:bg-yellow-300"
                    statusText = `(Turn ${player.turnNumber})`
                    statusIcon = <Clock className="h-4 w-4 ml-1" />
                  }

                  let playerDisplayName = player.name
                  if (player.llmModel) {
                    playerDisplayName = `${player.name} [${player.llmModel}]`
                  }

                  return (
                    <TabsTrigger key={player.id} value={player.id} className={triggerClass}>
                      <div className="flex items-center">
                        {playerDisplayName} {statusText}
                        {statusIcon}
                      </div>
                    </TabsTrigger>
                  )
                })}
              </TabsList>

              {Object.values(gameState.playerStates).map((player) => {
                const symbolsInUse = ALL_SYMBOLS.slice(0, gameState.gameConfig.baseSettings.numSymbols)

                return (
                  <TabsContent key={player.id} value={player.id} className="mt-2">
                    <Card className="h-full flex flex-col">
                      <CardHeader>
                        <CardTitle className="flex items-center justify-between">
                          <span>
                            Player: {player.name} {player.llmModel ? `[AI - ${player.llmModel}]` : "[AI]"}
                          </span>
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-slate-600">
                              Turn {player.turnNumber}
                              {player.isWaitingForLLM && (
                                <span className="ml-2 text-orange-600 animate-pulse">Thinking...</span>
                              )}
                            </span>
                          </div>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="flex-grow flex flex-col md:flex-row items-start justify-center gap-8 p-4">
                        <div className="flex-shrink-0">
                          <GameBoard
                            grid={player.grid}
                            onCellClick={() => {}} // 只读模式
                            selectedCells={player.selectedCells}
                            queriedCells={player.queriedCells}
                            isGuessing={player.isGuessing}
                            finalGuess={player.finalGuess}
                            masterPattern={gameState.masterPattern}
                            isGameOver={player.isFinished}
                            gridSize={gameState.gameConfig.baseSettings.gridSize}
                            symbolsInUse={symbolsInUse}
                            readOnly={true} // 强制只读
                          />
                        </div>
                        <div className="flex-grow flex flex-col space-y-4 w-full md:max-w-xs">
                          {player.score !== null && (
                            <Card className="p-4 bg-blue-50">
                              <p className="text-center text-blue-700 font-semibold">Score: {player.score}</p>
                            </Card>
                          )}
                          <div className="flex-grow">
                            <GameLog log={player.log} />
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  </TabsContent>
                )
              })}
            </Tabs>
          </div>
        ) : (
          // 显示测试集概览
          <Card className="max-w-2xl mx-auto">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Trophy className="h-5 w-5" />
                {testSet.status === "completed"
                  ? "Test Set Completed"
                  : testSet.status === "running"
                    ? "Test Set Running"
                    : testSet.status === "failed"
                      ? "Test Set Failed"
                      : "Test Set Ready"}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Progress Section */}
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span>Overall Progress</span>
                  <span>
                    {testSet.completed_games}/{testSet.total_games} games
                  </span>
                </div>
                <Progress value={(testSet.completed_games / testSet.total_games) * 100} className="w-full h-3" />
                <div className="text-xs text-muted-foreground text-center">
                  {testSet.status === "running" && "Running in background..."}
                  {testSet.status === "completed" && "All games completed!"}
                  {testSet.status === "failed" && "Execution failed"}
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 gap-4">
                <div className="flex items-center gap-2">
                  <Users className="h-4 w-4 text-slate-500" />
                  <span className="text-sm">{testSet.config?.participants?.length || 0} Participants</span>
                </div>
                <div className="flex items-center gap-2">
                  <Clock className="h-4 w-4 text-slate-500" />
                  <span className="text-sm">{testSet.total_games} Total Games</span>
                </div>
              </div>

              {/* Participants */}
              <div className="text-sm text-slate-600">
                <p className="font-medium mb-2">Participants:</p>
                <ul className="list-disc list-inside space-y-1">
                  {testSet.config?.participants?.map((participant, index) => (
                    <li key={index}>
                      {participant.model_name}
                      {participant.model_params && (
                        <Badge variant="secondary" className="ml-2 text-xs">
                          Custom Params
                        </Badge>
                      )}
                    </li>
                  ))}
                </ul>
              </div>

              {/* Game Configuration */}
              <div className="text-sm text-slate-600">
                <p className="font-medium mb-2">Game Configuration:</p>
                <ul className="space-y-1">
                  {testSet.config?.games?.map((game, index) => (
                    <li key={index} className="flex items-center justify-between">
                      <span>
                        {game.grid_size}×{game.grid_size} grid, {game.num_symbols} symbols
                      </span>
                      <Badge variant="outline" className="text-xs">
                        ×{game.repeat_count}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Actions */}
              <div className="flex justify-center space-x-4">
                {(testSet.status === "created" || testSet.status === "pending") && (
                  <Button onClick={handleStartTestSet} size="lg">
                    <Trophy className="mr-2 h-5 w-5" />
                    Start Test Set
                  </Button>
                )}

                {testSet.status === "running" && (
                  <div className="text-center">
                    <div className="flex items-center justify-center gap-2 text-blue-600 mb-2">
                      <RefreshCw className="h-5 w-5 animate-spin" />
                      <span className="font-medium">Running in background</span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      Games are being executed automatically.{" "}
                      {gameState ? "Watch the live game above!" : "Refresh to see if a game is active."}
                    </p>
                  </div>
                )}

                {testSet.status === "completed" && (
                  <Button asChild size="lg">
                    <Link href={`/leaderboard?testSetId=${testSetId}`}>
                      <Trophy className="mr-2 h-5 w-5" />
                      View Results
                    </Link>
                  </Button>
                )}

                {(testSet.status === "failed" || testSet.status === "pending") && (
                  <Button onClick={handleStartTestSet} size="lg" variant="outline">
                    <RefreshCw className="mr-2 h-5 w-5" />
                    {testSet.status === "pending" ? "Start Test Set" : "Retry Test Set"}
                  </Button>
                )}
              </div>

              {/* Metadata */}
              <div className="text-xs text-muted-foreground text-center pt-4 border-t">
                Created: {formatDate(testSet.created_at)}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
