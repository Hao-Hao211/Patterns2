"use client"

import { useState, useEffect, useRef, useCallback } from "react"
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
import { ArrowLeft, Trophy, Clock, Users, CheckCircle, RefreshCw, Eye, Brain, Eraser, Send } from "lucide-react"
import type { Grid, Symbol } from "@/types/game-types"

const ALL_SYMBOLS: Symbol[] = ["○", "△", "✖", "□", "★", "+"]

interface TestSetConfig {
  participants: Array<{
    participant_type?: 'Human' | 'LLM'
    human_name?: string
    model_name?: string
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
  join_token?: string
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
  isWaitingForHuman?: boolean
  isPaused: boolean
}

interface Position {
  row: number
  col: number
}

interface GameState {
  gameId: string
  gameIndex: number
  gameConfig: any
  masterPattern: Grid | null
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
  const [humanGameStates, setHumanGameStates] = useState<Record<number, GameState>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<string>("")

  // Use ref to track user selection to prevent re-renders from affecting it
  const userSelectedTabRef = useRef(false)
  const lastGameIdRef = useRef<string>("")
  const testSetConfigRef = useRef<TestSetConfig | null>(null)

  // Human player interaction state
  const [selectedCells, setSelectedCells] = useState<Position[]>([])
  const [isGuessMode, setIsGuessMode] = useState(false)
  const [guessGrid, setGuessGrid] = useState<string[][]>([])
  const [submitting, setSubmitting] = useState(false)

  // Remote human test state
  const [isRemoteHumanTest, setIsRemoteHumanTest] = useState(false)
  const [joinToken, setJoinToken] = useState<string | null>(null)
  const [joinUrl, setJoinUrl] = useState<string | null>(null)
  const [scoreboard, setScoreboard] = useState<any[]>([])

  // Detect remote human test
  useEffect(() => {
    if (testSet?.join_token) {
      setIsRemoteHumanTest(true)
      setJoinToken(testSet.join_token)
      setJoinUrl(`${window.location.origin}/join/${testSet.join_token}`)
    }
  }, [testSet])

  // Scoreboard fetching for remote human tests
  const fetchScoreboard = useCallback(async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/scoreboard`)
      if (res.ok) {
        const data = await res.json()
        const entries = data.players || data.entries || (Array.isArray(data) ? data : [])
        const mapped = entries.map((e: any) => ({
          participant_index: e.participant_index,
          player_name: e.name || e.player_name,
          current_game: (e.current_game_index ?? e.current_game ?? 0) + 1,
          total_games: e.total_games ?? 0,
          total_score: e.cumulative_score ?? e.total_score ?? 0,
          is_finished: e.is_finished ?? false,
        }))
        setScoreboard(mapped)
      }
    } catch {
      // silent
    }
  }, [testSetId])

  // Scoreboard polling for remote human tests
  useEffect(() => {
    if (isRemoteHumanTest && testSet?.status === 'running') {
      fetchScoreboard()
      const interval = setInterval(fetchScoreboard, 3000)
      return () => clearInterval(interval)
    }
  }, [isRemoteHumanTest, testSet?.status, fetchScoreboard])

  // Build combined game state: merge LLM game state with human game states
  const hasHumanParticipants = testSet?.config?.participants?.some(p => p.participant_type === 'Human') || false
  const combinedGameState: GameState | null = (() => {
    if (!hasHumanParticipants) return gameState  // No humans, use LLM state as-is

    const humanStateEntries = Object.values(humanGameStates)
    if (!gameState && humanStateEntries.length === 0) return null

    // Start from LLM game state or build a base from the first human state
    const base = gameState || humanStateEntries[0]
    if (!base) return null

    // Merge all player states from human games into combined state
    const mergedPlayerStates: Record<string, PlayerState> = {
      ...(gameState?.playerStates || {})
    }
    for (const hgs of humanStateEntries) {
      if (hgs.playerStates) {
        for (const [pid, ps] of Object.entries(hgs.playerStates)) {
          mergedPlayerStates[pid] = ps
        }
      }
    }

    // Determine overall phase: "playing" if any game is still playing
    const allPhases = [
      gameState?.currentPhase,
      ...humanStateEntries.map(h => h.currentPhase)
    ].filter(Boolean)
    const combinedPhase = allPhases.includes('playing') ? 'playing' : 'results'

    return {
      ...base,
      playerStates: mergedPlayerStates,
      currentPhase: combinedPhase as "playing" | "results",
      allPlayersFinished: Object.values(mergedPlayerStates).every(ps => ps.isFinished),
      masterPattern: gameState?.masterPattern || humanStateEntries.find(h => h.masterPattern)?.masterPattern || base.masterPattern,
    }
  })()

  useEffect(() => {
    // Initial load: fetch test set first, then human states (which depend on config)
    const init = async () => {
      await fetchTestSet()
      fetchCurrentGame()
      fetchHumanGameStates()
    }
    init()

    const interval = setInterval(() => {
      fetchTestSet()
      fetchCurrentGame()
      fetchHumanGameStates()
    }, 2000)

    return () => clearInterval(interval)
  }, [testSetId])

  const fetchTestSet = async () => {
    try {
      if (loading) {
        setError(null)
      }

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}`)
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
      testSetConfigRef.current = testSetData.config || null
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
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/current-game`)
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

  const fetchHumanGameStates = async () => {
    const config = testSetConfigRef.current || testSet?.config
    if (!config?.participants) return
    const humanParticipants = config.participants
      .map((p, i) => ({ ...p, index: i }))
      .filter(p => p.participant_type === 'Human')

    if (humanParticipants.length === 0) return

    const newStates: Record<number, GameState> = {}
    for (const hp of humanParticipants) {
      try {
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/current-game?player_index=${hp.index}`
        )
        if (res.ok) {
          const data = await res.json()
          if (data.gameId) {
            newStates[hp.index] = data
          }
        }
      } catch (err) {
        // Silent
      }
    }
    setHumanGameStates(newStates)
  }

  const handleStartTestSet = async () => {
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/start`, {
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
    // Reset human interaction state when switching tabs
    setSelectedCells([])
    setIsGuessMode(false)
  }

  // Find the correct game ID for a player (could be in main state or human state)
  const getGameIdForPlayer = useCallback((playerId: string): string | undefined => {
    // Check human game states first
    for (const hgs of Object.values(humanGameStates)) {
      if (hgs.playerStates && playerId in hgs.playerStates) {
        return hgs.gameId
      }
    }
    // Fall back to main game state
    return gameState?.gameId
  }, [humanGameStates, gameState])

  // Initialize guess grid when entering guess mode
  const initGuessGrid = useCallback((gridSize: number) => {
    setGuessGrid(Array(gridSize).fill(null).map(() => Array(gridSize).fill("?")))
  }, [])

  // Handle cell click for human players
  const handleHumanCellClick = useCallback((row: number, col: number, player: PlayerState) => {
    if (player.isFinished || !player.isWaitingForHuman) return

    const gridSize = combinedGameState?.gameConfig?.baseSettings?.gridSize || 6
    const isObservedCell = player.queriedCells.some(p => p.row === row && p.col === col)

    if (isGuessMode) {
      // In guess mode: cycle through symbols
      if (isObservedCell) return // Can't change observed cells
      const symbolsInUse = ALL_SYMBOLS.slice(0, combinedGameState?.gameConfig?.baseSettings?.numSymbols || 5)
      setGuessGrid(prev => {
        const newGrid = prev.map(r => [...r])
        const currentSymbol = newGrid[row][col]
        const currentIndex = currentSymbol === "?" ? -1 : symbolsInUse.indexOf(currentSymbol as Symbol)
        const nextIndex = (currentIndex + 1) % symbolsInUse.length
        newGrid[row][col] = symbolsInUse[nextIndex]
        return newGrid
      })
    } else {
      // In observe mode: toggle cell selection (max 3)
      if (String(player.grid[row][col]) !== "?") return // Already observed
      setSelectedCells(prev => {
        const isSelected = prev.some(p => p.row === row && p.col === col)
        if (isSelected) {
          return prev.filter(p => !(p.row === row && p.col === col))
        } else if (prev.length >= 3) {
          return prev // Max 3 cells
        } else {
          return [...prev, { row, col }]
        }
      })
    }
  }, [isGuessMode, combinedGameState])

  // Submit observe action
  const handleHumanObserve = useCallback(async (player: PlayerState) => {
    if (selectedCells.length === 0 || submitting) return
    setSubmitting(true)
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/human-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            game_id: getGameIdForPlayer(player.id),
            player_id: player.id,
            action: "observe",
            cells_to_observe: selectedCells,
          }),
        }
      )
      if (response.ok) {
        setSelectedCells([])
        await fetchCurrentGame(); await fetchHumanGameStates()
      }
    } catch (err) {
      console.error("Failed to submit observe action:", err)
    } finally {
      setSubmitting(false)
    }
  }, [selectedCells, submitting, testSetId, getGameIdForPlayer])

  // Submit guess action
  const handleHumanGuess = useCallback(async (player: PlayerState) => {
    if (submitting) return

    // Build final guess: observed cells keep their values, unobserved use guessGrid
    const gridSize = combinedGameState?.gameConfig?.baseSettings?.gridSize || 6
    const finalGuess = player.grid.map((row, r) =>
      row.map((cell, c) => {
        if (cell !== null && String(cell) !== "?") return String(cell) // Observed cell
        return guessGrid[r]?.[c] || "?"
      })
    )

    // Check for unguessed cells
    const unguessedCells: string[] = []
    finalGuess.forEach((row, r) =>
      row.forEach((cell, c) => {
        if (cell === "?") {
          unguessedCells.push(`${String.fromCharCode(65 + c)}${r + 1}`)
        }
      })
    )
    if (unguessedCells.length > 0) {
      alert(`${unguessedCells.length} cell(s) still unguessed: ${unguessedCells.join(", ")}. Please fill in all cells before submitting.`)
      return
    }

    setSubmitting(true)
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/human-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            game_id: getGameIdForPlayer(player.id),
            player_id: player.id,
            action: "guess",
            guess_grid: finalGuess,
          }),
        }
      )
      if (response.ok) {
        setIsGuessMode(false)
        await fetchCurrentGame(); await fetchHumanGameStates()
        setGuessGrid([])
      }
    } catch (err) {
      console.error("Failed to submit guess:", err)
    } finally {
      setSubmitting(false)
    }
  }, [submitting, testSetId, combinedGameState, guessGrid, getGameIdForPlayer])

  // Submit give up action
  const handleHumanGiveUp = useCallback(async (player: PlayerState) => {
    if (submitting) return
    setSubmitting(true)
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/human-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            game_id: getGameIdForPlayer(player.id),
            player_id: player.id,
            action: "give_up",
          }),
        }
      )
      if (response.ok) {
        await fetchCurrentGame(); await fetchHumanGameStates()
      }
    } catch (err) {
      console.error("Failed to submit give up:", err)
    } finally {
      setSubmitting(false)
    }
  }, [submitting, testSetId, getGameIdForPlayer])

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
        {combinedGameState ? (
          // 显示当前游戏状态
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Eye className="h-5 w-5" />
                  Live Game View{!hasHumanParticipants && ` - Game ${combinedGameState.gameIndex + 1}/${testSet.total_games}`}
                  {combinedGameState.currentPhase === "playing" && (
                    <Badge variant="default" className="ml-2">
                      <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                      In Progress
                    </Badge>
                  )}
                  {combinedGameState.currentPhase === "results" && (
                    <Badge variant="secondary" className="ml-2">
                      <CheckCircle className="h-3 w-3 mr-1" />
                      Completed
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
            </Card>

            {/* Join URL for remote human tests */}
            {isRemoteHumanTest && joinUrl && (
              <Card className="p-4 bg-blue-50 border-blue-200">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-blue-700">Join URL</p>
                    <a href={joinUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-600 hover:underline font-mono">
                      {joinUrl}
                    </a>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      navigator.clipboard.writeText(joinUrl)
                    }}
                  >
                    Copy Link
                  </Button>
                </div>
              </Card>
            )}

            {/* Live Scoreboard for remote human tests */}
            {isRemoteHumanTest && scoreboard.length > 0 && (
              <Card>
                <CardHeader className="py-3">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Trophy className="h-4 w-4 text-yellow-500" />
                    Live Scoreboard
                  </CardTitle>
                </CardHeader>
                <CardContent className="py-0 pb-3">
                  <div className="rounded-lg border overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-50">
                        <tr>
                          <th className="px-3 py-1.5 text-left font-medium text-slate-600">#</th>
                          <th className="px-3 py-1.5 text-left font-medium text-slate-600">Player</th>
                          <th className="px-3 py-1.5 text-center font-medium text-slate-600">Game</th>
                          <th className="px-3 py-1.5 text-right font-medium text-slate-600">Score</th>
                          <th className="px-3 py-1.5 text-center font-medium text-slate-600">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {scoreboard
                          .sort((a: any, b: any) => b.total_score - a.total_score)
                          .map((entry: any, idx: number) => (
                            <tr key={entry.participant_index} className={idx % 2 === 0 ? "bg-white" : "bg-slate-50"}>
                              <td className="px-3 py-1.5">{idx + 1}</td>
                              <td className="px-3 py-1.5">{entry.player_name}</td>
                              <td className="px-3 py-1.5 text-center">{entry.current_game}/{entry.total_games}</td>
                              <td className="px-3 py-1.5 text-right">{entry.total_score}</td>
                              <td className="px-3 py-1.5 text-center">
                                <span className={`text-xs px-2 py-0.5 rounded-full ${entry.is_finished ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'}`}>
                                  {entry.is_finished ? "Done" : "Playing"}
                                </span>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* 游戏界面 */}
            <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
              <TabsList
                className="grid w-full gap-1"
                style={{ gridTemplateColumns: `repeat(${Object.keys(combinedGameState.playerStates).length}, minmax(0, 1fr))` }}
              >
                {Object.values(combinedGameState.playerStates).map((player) => {
                  let triggerClass = "data-[state=active]:bg-slate-200"
                  let statusText = ""
                  let statusIcon = null

                  if (player.isFinished) {
                    triggerClass = "bg-green-200 text-green-800 data-[state=active]:bg-green-300"
                    statusText = "(Done)"
                    statusIcon = <CheckCircle className="h-4 w-4 ml-1" />
                  } else if (player.isWaitingForHuman) {
                    triggerClass = "bg-blue-200 text-blue-800 data-[state=active]:bg-blue-300 animate-pulse"
                    statusText = "(Your Turn)"
                    statusIcon = <Users className="h-4 w-4 ml-1" />
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
                  if (player.type === 'Human') {
                    // Find this human's game index from their individual game state
                    const humanState = Object.values(humanGameStates).find(
                      hgs => hgs.playerStates && player.id in hgs.playerStates
                    )
                    const gameNum = humanState ? humanState.gameIndex + 1 : ''
                    const totalHumanGames = testSet?.config?.games?.reduce((sum, g) => sum + (g.repeat_count || 1), 0) || '?'
                    playerDisplayName = `${player.name} [Human]${gameNum ? ` Game ${gameNum}/${totalHumanGames}` : ''}`
                  } else if (player.llmModel) {
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

              {Object.values(combinedGameState.playerStates).map((player) => {
                const symbolsInUse = ALL_SYMBOLS.slice(0, combinedGameState.gameConfig.baseSettings.numSymbols)
                const gridSize = combinedGameState.gameConfig.baseSettings.gridSize
                const isHuman = player.type === 'Human'
                const isHumanTurn = isHuman && player.isWaitingForHuman && !player.isFinished

                // Build display grid: always overlay guesses on truth grid (like original)
                let displayGrid = player.grid
                if (isHuman && guessGrid.length > 0 && player.id === activeTab) {
                  displayGrid = player.grid.map((row, r) =>
                    row.map((cell, c) => {
                      const guessVal = guessGrid[r]?.[c]
                      return (guessVal && guessVal !== "?" ? guessVal : cell) as any
                    })
                  )
                }

                return (
                  <TabsContent key={player.id} value={player.id} className="mt-2">
                    <Card className="h-full flex flex-col">
                      <CardHeader>
                        <CardTitle className="flex items-center justify-between">
                          <span>
                            Player: {player.name} {isHuman ? '[Human]' : player.llmModel ? `[AI - ${player.llmModel}]` : "[AI]"}
                          </span>
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-slate-600">
                              Turn {player.turnNumber}
                              {player.isWaitingForLLM && (
                                <span className="ml-2 text-orange-600 animate-pulse">Thinking...</span>
                              )}
                              {player.isWaitingForHuman && !player.isFinished && !isRemoteHumanTest && (
                                <span className="ml-2 text-blue-600 animate-pulse">Waiting for your action...</span>
                              )}
                            </span>
                          </div>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="flex-grow flex flex-col md:flex-row items-start justify-center gap-8 p-4">
                        <div className="flex-shrink-0">
                          <GameBoard
                            grid={displayGrid}
                            onCellClick={isHumanTurn && !isRemoteHumanTest ? (row: number, col: number) => handleHumanCellClick(row, col, player) : () => {}}
                            selectedCells={isHumanTurn && !isGuessMode ? selectedCells : player.selectedCells}
                            queriedCells={player.queriedCells}
                            isGuessing={!!(isGuessMode && isHumanTurn)}
                            finalGuess={player.finalGuess}
                            masterPattern={combinedGameState.masterPattern || Array.from({ length: gridSize }, () => Array(gridSize).fill(null))}
                            isGameOver={player.isFinished}
                            gridSize={gridSize}
                            symbolsInUse={symbolsInUse}
                            readOnly={!isHumanTurn || isRemoteHumanTest}
                          />
                        </div>
                        <div className="flex-grow flex flex-col space-y-4 w-full md:max-w-xs">
                          {player.score !== null && (
                            <Card className="p-4 bg-blue-50">
                              <p className="text-center text-blue-700 font-semibold">Score: {player.score}</p>
                            </Card>
                          )}

                          {/* Human player controls */}
                          {isHumanTurn && !isRemoteHumanTest && (
                            <Card className="p-4 bg-blue-50 space-y-3">
                              {!isGuessMode ? (
                                <>
                                  <p className="text-sm text-blue-700 font-medium">
                                    Select up to 3 cells to observe ({selectedCells.length}/3 selected)
                                  </p>
                                  <div className="flex flex-col gap-2">
                                    <Button
                                      size="sm"
                                      onClick={() => handleHumanObserve(player)}
                                      disabled={selectedCells.length === 0 || submitting}
                                    >
                                      <Eye className="h-4 w-4 mr-2" />
                                      Observe Selected Cells
                                    </Button>
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() => {
                                        setIsGuessMode(true)
                                        if (guessGrid.length === 0) initGuessGrid(gridSize)
                                        setSelectedCells([])
                                      }}
                                    >
                                      <Brain className="h-4 w-4 mr-2" />
                                      Guess
                                    </Button>
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      className="text-red-600"
                                      onClick={() => handleHumanGiveUp(player)}
                                      disabled={submitting}
                                    >
                                      Give Up
                                    </Button>
                                  </div>
                                </>
                              ) : (
                                <>
                                  <p className="text-sm text-blue-700 font-medium">
                                    Click cells to place your guesses.
                                  </p>
                                  <div className="flex flex-col gap-2">
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() => {
                                        setIsGuessMode(false)
                                      }}
                                    >
                                      <ArrowLeft className="h-4 w-4 mr-2" />
                                      Back to Observe
                                    </Button>
                                    <Button
                                      size="sm"
                                      variant="destructive"
                                      onClick={() => initGuessGrid(gridSize)}
                                    >
                                      <Eraser className="h-4 w-4 mr-2" />
                                      Erase All Guesses
                                    </Button>
                                    <Button
                                      size="sm"
                                      className="bg-green-600 hover:bg-green-700"
                                      onClick={() => handleHumanGuess(player)}
                                      disabled={submitting}
                                    >
                                      <Send className="h-4 w-4 mr-2" />
                                      Submit Final Guess
                                    </Button>
                                  </div>
                                </>
                              )}
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
                  {testSet.config?.participants?.map((participant: any, index: number) => (
                    <li key={index}>
                      {participant.participant_type === 'Human'
                        ? 'Human Player'
                        : participant.model_name}
                      {participant.participant_type === 'Human' && (
                        <Badge variant="secondary" className="ml-2 text-xs bg-blue-100 text-blue-700">
                          Human
                        </Badge>
                      )}
                      {participant.participant_type !== 'Human' && participant.model_params && (
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
                      {combinedGameState ? "Watch the live game above!" : "Refresh to see if a game is active."}
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
