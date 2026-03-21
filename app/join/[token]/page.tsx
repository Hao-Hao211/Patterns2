"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { useParams } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { GameBoard } from "@/components/game-board"
import { GameLog } from "@/components/game-log"
import {
  Trophy,
  Clock,
  Users,
  CheckCircle,
  RefreshCw,
  Eye,
  Brain,
  LogIn,
  Loader2,
  AlertCircle,
  ArrowLeft,
  Eraser,
  Send,
} from "lucide-react"
import type { Grid, Symbol, Position } from "@/types/game-types"

const ALL_SYMBOLS: Symbol[] = ["○", "△", "✖", "□", "★", "+"]

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL

// --- Types ---

interface JoinInfo {
  test_set_id: string
  name: string
  description?: string
  status: string
  participant_count: number
  total_games: number
  participant_slots: Array<{ index: number; claimed: boolean; player_name: string | null }>
}

interface SessionData {
  session_token: string
  participant_index: number
  player_name: string
  test_set_id: string
}

interface PlayerState {
  id: string
  name: string
  type: string
  grid: Grid
  queriedCells: Position[]
  selectedCells: Position[]
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

interface GameState {
  gameId: string
  gameIndex: number
  gameConfig: {
    baseSettings: {
      gridSize: number
      numSymbols: number
    }
  }
  masterPattern: Grid | null
  currentPhase: "playing" | "results"
  playerStates: Record<string, PlayerState>
  allPlayersFinished: boolean
  currentTurn: number
}

interface ScoreboardEntry {
  participant_index: number
  player_name: string
  current_game: number
  total_games: number
  total_score: number
  status: string
  is_finished: boolean
}

// --- Phase enum ---
type Phase = "join" | "waiting" | "playing" | "game_results" | "all_complete"

// --- Component ---

export default function JoinPage() {
  const params = useParams()
  const token = params.token as string

  // Phase
  const [phase, setPhase] = useState<Phase>("join")

  // Join phase state
  const [joinInfo, setJoinInfo] = useState<JoinInfo | null>(null)
  const [playerName, setPlayerName] = useState("")
  const [joining, setJoining] = useState(false)
  const [session, setSession] = useState<SessionData | null>(null)

  // Game phase state
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [scoreboard, setScoreboard] = useState<ScoreboardEntry[]>([])
  const [myPlayer, setMyPlayer] = useState<PlayerState | null>(null)

  // Human interaction state
  const [selectedCells, setSelectedCells] = useState<Position[]>([])
  const [isGuessMode, setIsGuessMode] = useState(false)
  const [guessGrid, setGuessGrid] = useState<string[][]>([])
  const [submitting, setSubmitting] = useState(false)
  const [readySubmitted, setReadySubmitted] = useState(false)

  // General state
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Completed games tracking
  const [completedGames, setCompletedGames] = useState<
    Array<{ gameIndex: number; score: number | null }>
  >([])
  const lastGameIdRef = useRef<string>("")

  // --- localStorage helpers ---
  const storageKey = `patterns2_session_${token}`

  const saveSession = useCallback(
    (data: SessionData) => {
      localStorage.setItem(storageKey, JSON.stringify(data))
      setSession(data)
    },
    [storageKey]
  )

  const loadSession = useCallback((): SessionData | null => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) return JSON.parse(raw) as SessionData
    } catch {
      // ignore
    }
    return null
  }, [storageKey])

  // --- API helpers ---

  const fetchJoinInfo = useCallback(async (): Promise<JoinInfo | null> => {
    try {
      const res = await fetch(`${API_BASE}/api/join/${token}`)
      if (res.status === 404) throw new Error("This game session no longer exists. It may have been deleted.")
      if (!res.ok) throw new Error("Failed to load game info. Please try again.")
      const data: JoinInfo = await res.json()
      setJoinInfo(data)
      return data
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load join info")
      return null
    }
  }, [token])

  const resumeSession = useCallback(
    async (saved: SessionData): Promise<boolean> => {
      try {
        const res = await fetch(
          `${API_BASE}/api/join/${token}/session/${saved.session_token}`
        )
        if (res.ok) {
          const data = await res.json()
          setSession(saved)
          // Determine phase from response
          if (data.status === "waiting_for_players") {
            setPhase("waiting")
          } else if (data.status === "running" || data.status === "playing") {
            setPhase("playing")
          } else if (data.status === "completed") {
            setPhase("all_complete")
          } else {
            setPhase("waiting")
          }
          return true
        }
      } catch {
        // Session invalid, clear it
      }
      localStorage.removeItem(storageKey)
      return false
    },
    [token, storageKey]
  )

  const fetchGameState = useCallback(async () => {
    if (!session) return
    try {
      const res = await fetch(
        `${API_BASE}/api/test-sets/${session.test_set_id}/current-game?player_index=${session.participant_index}`
      )
      if (!res.ok) return
      const data: GameState = await res.json()
      if (!data.gameId) {
        // No active game - could be between games or all done
        setGameState(null)
        return
      }

      // Detect new game
      if (lastGameIdRef.current && lastGameIdRef.current !== data.gameId) {
        // New game started - reset interaction state
        setSelectedCells([])
        setIsGuessMode(false)
        setGuessGrid([])
        setReadySubmitted(false)
      }
      lastGameIdRef.current = data.gameId

      setGameState(data)

      // Find my player
      const players = Object.values(data.playerStates)
      const me =
        players.find(
          (p) =>
            p.name === session.player_name && p.type === "Human"
        ) || players[0]
      setMyPlayer(me || null)

      // Determine sub-phase
      if (me) {
        if (me.isFinished && data.allPlayersFinished) {
          // Check if there are more games or all done
          setPhase("game_results")
        } else if (me.isFinished) {
          setPhase("game_results")
        } else {
          setPhase("playing")
        }
      }
    } catch {
      // silent
    }
  }, [session])

  const fetchScoreboard = useCallback(async () => {
    if (!session) return
    try {
      const res = await fetch(
        `${API_BASE}/api/test-sets/${session.test_set_id}/scoreboard`
      )
      if (res.ok) {
        const data = await res.json()
        const entries = data.players || data.entries || (Array.isArray(data) ? data : [])
        // Map backend field names to frontend field names
        const mapped = entries.map((e: any) => ({
          participant_index: e.participant_index,
          player_name: e.name || e.player_name,
          current_game: (e.current_game_index ?? e.current_game ?? 0) + 1,
          total_games: e.total_games ?? 0,
          total_score: e.cumulative_score ?? e.total_score ?? 0,
          is_finished: e.is_finished ?? false,
          status: e.status || (e.is_finished ? "Done" : "Playing"),
        }))
        setScoreboard(mapped)
      }
    } catch {
      // silent
    }
  }, [session])

  // --- Initialization ---

  useEffect(() => {
    const init = async () => {
      setLoading(true)
      // Check for saved session
      const saved = loadSession()
      if (saved) {
        const resumed = await resumeSession(saved)
        if (resumed) {
          setLoading(false)
          return
        }
      }
      // No session, fetch join info
      await fetchJoinInfo()
      setLoading(false)
    }
    init()
  }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Polling ---

  useEffect(() => {
    if (phase === "waiting") {
      let stopped = false
      const interval = setInterval(async () => {
        if (stopped) return
        const info = await fetchJoinInfo()
        if (!info) {
          // Token invalid or test set deleted — stop polling
          stopped = true
          clearInterval(interval)
          return
        }
        if (info.status === "running" || info.status === "playing") {
          setPhase("playing")
        }
      }, 3000)
      return () => { stopped = true; clearInterval(interval) }
    }
  }, [phase, fetchJoinInfo])

  useEffect(() => {
    if ((phase === "playing" || phase === "game_results") && session) {
      fetchGameState()
      fetchScoreboard()
      const gameInterval = setInterval(fetchGameState, 2000)
      const scoreInterval = setInterval(fetchScoreboard, 3000)
      return () => {
        clearInterval(gameInterval)
        clearInterval(scoreInterval)
      }
    }
  }, [phase, session, fetchGameState, fetchScoreboard])

  // Keep polling scoreboard in all_complete phase (while waiting for others)
  useEffect(() => {
    if (phase === "all_complete" && session) {
      fetchScoreboard()
      // Poll until all players are done
      const interval = setInterval(fetchScoreboard, 3000)
      return () => clearInterval(interval)
    }
  }, [phase, session, fetchScoreboard])

  // Detect all complete
  useEffect(() => {
    if (phase === "game_results" && session) {
      // Check if no current game state (state key cleaned up)
      if (!gameState) {
        fetchJoinInfo().then((info) => {
          if (info && (info.status === "completed" || info.status === "running")) {
            // Check scoreboard to see if THIS player is done
            const myEntry = scoreboard.find(e => e.participant_index === session.participant_index)
            if (myEntry?.is_finished || info.status === "completed") {
              setPhase("all_complete")
            }
          }
        })
      }
    }
  }, [phase, gameState, session, fetchJoinInfo, scoreboard])

  // --- Handlers ---

  const handleJoin = async () => {
    const trimmed = playerName.trim()
    if (!trimmed) return
    setJoining(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/api/join/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player_name: trimmed }),
      })
      if (!res.ok) {
        const errData = await res.json().catch(() => null)
        throw new Error(
          errData?.detail || errData?.message || "Failed to join"
        )
      }
      const data = await res.json()
      const sessionData: SessionData = {
        session_token: data.session_token,
        participant_index: data.participant_index,
        player_name: trimmed,
        test_set_id: data.test_set_id || joinInfo?.test_set_id || "",
      }
      saveSession(sessionData)
      // Refresh join info
      const info = await fetchJoinInfo()
      if (info && (info.status === "running" || info.status === "playing")) {
        setPhase("playing")
      } else {
        setPhase("waiting")
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to join")
    } finally {
      setJoining(false)
    }
  }

  const initGuessGrid = useCallback((gridSize: number) => {
    setGuessGrid(
      Array(gridSize)
        .fill(null)
        .map(() => Array(gridSize).fill("?"))
    )
  }, [])

  const handleCellClick = useCallback(
    (row: number, col: number) => {
      if (!myPlayer || myPlayer.isFinished || !myPlayer.isWaitingForHuman)
        return
      if (!gameState) return

      const isObservedCell = myPlayer.queriedCells.some(
        (p) => p.row === row && p.col === col
      )

      if (isGuessMode) {
        if (isObservedCell) return
        const numSymbols =
          gameState.gameConfig?.baseSettings?.numSymbols || 5
        const symbolsInUse = ALL_SYMBOLS.slice(0, numSymbols)
        setGuessGrid((prev) => {
          const newGrid = prev.map((r) => [...r])
          const currentSymbol = newGrid[row][col]
          const currentIndex =
            currentSymbol === "?"
              ? -1
              : symbolsInUse.indexOf(currentSymbol as Symbol)
          const nextIndex = (currentIndex + 1) % symbolsInUse.length
          newGrid[row][col] = symbolsInUse[nextIndex]
          return newGrid
        })
      } else {
        // Observe mode: toggle cell selection (max 3)
        if (String(myPlayer.grid[row][col]) !== "?") return
        setSelectedCells((prev) => {
          const isSelected = prev.some(
            (p) => p.row === row && p.col === col
          )
          if (isSelected) {
            return prev.filter(
              (p) => !(p.row === row && p.col === col)
            )
          } else if (prev.length >= 3) {
            return prev
          } else {
            return [...prev, { row, col }]
          }
        })
      }
    },
    [myPlayer, gameState, isGuessMode]
  )

  const handleObserve = useCallback(async () => {
    if (
      selectedCells.length === 0 ||
      submitting ||
      !session ||
      !gameState ||
      !myPlayer
    )
      return
    setSubmitting(true)
    try {
      const res = await fetch(
        `${API_BASE}/api/test-sets/${session.test_set_id}/human-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            game_id: gameState.gameId,
            player_id: myPlayer.id,
            action: "observe",
            cells_to_observe: selectedCells,
            session_token: session.session_token,
          }),
        }
      )
      if (res.ok) {
        // Clear guesses for observed cells (like original game)
        if (guessGrid.length > 0) {
          setGuessGrid(prev => {
            const newGrid = prev.map(r => [...r])
            selectedCells.forEach(({ row, col }) => {
              if (newGrid[row]?.[col]) newGrid[row][col] = "?"
            })
            return newGrid
          })
        }
        setSelectedCells([])
        await fetchGameState()
      }
    } catch (err) {
      console.error("Observe failed:", err)
    } finally {
      setSubmitting(false)
    }
  }, [selectedCells, submitting, session, gameState, myPlayer, fetchGameState, guessGrid])

  const handleGuess = useCallback(async () => {
    if (submitting || !session || !gameState || !myPlayer) return

    const finalGuess = myPlayer.grid.map((row, r) =>
      row.map((cell, c) => {
        if (cell !== null && String(cell) !== "?") return String(cell)
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
      alert(
        `${unguessedCells.length} cell(s) still unguessed: ${unguessedCells.join(", ")}. Please fill in all cells before submitting.`
      )
      return
    }

    setSubmitting(true)
    try {
      const res = await fetch(
        `${API_BASE}/api/test-sets/${session.test_set_id}/human-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            game_id: gameState.gameId,
            player_id: myPlayer.id,
            action: "guess",
            guess_grid: finalGuess,
            session_token: session.session_token,
          }),
        }
      )
      if (res.ok) {
        setIsGuessMode(false)
        setGuessGrid([])
        await fetchGameState()
      }
    } catch (err) {
      console.error("Guess failed:", err)
    } finally {
      setSubmitting(false)
    }
  }, [submitting, session, gameState, myPlayer, guessGrid, fetchGameState])

  const handleGiveUp = useCallback(async () => {
    if (submitting || !session || !gameState || !myPlayer) return
    setSubmitting(true)
    try {
      const res = await fetch(
        `${API_BASE}/api/test-sets/${session.test_set_id}/human-action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            game_id: gameState.gameId,
            player_id: myPlayer.id,
            action: "give_up",
            session_token: session.session_token,
          }),
        }
      )
      if (res.ok) {
        await fetchGameState()
      }
    } catch (err) {
      console.error("Give up failed:", err)
    } finally {
      setSubmitting(false)
    }
  }, [submitting, session, gameState, myPlayer, fetchGameState])

  const handleNextGame = useCallback(async () => {
    if (!session || readySubmitted) return
    setReadySubmitted(true)
    try {
      // Track completed game (deduplicate by gameIndex)
      if (gameState && myPlayer) {
        setCompletedGames((prev) => {
          if (prev.some(g => g.gameIndex === gameState.gameIndex)) return prev
          return [...prev, { gameIndex: gameState.gameIndex, score: myPlayer.score }]
        })
      }
      const res = await fetch(
        `${API_BASE}/api/test-sets/${session.test_set_id}/human-ready`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_token: session.session_token,
            participant_index: session.participant_index,
          }),
        }
      )
      if (!res.ok) {
        throw new Error("Failed to signal ready")
      }
      // Reset interaction state for next game
      setSelectedCells([])
      setIsGuessMode(false)
      setGuessGrid([])
      // Polling will detect next game
    } catch (err) {
      console.error("Ready failed:", err)
      setReadySubmitted(false)
    }
  }, [session, gameState, myPlayer, readySubmitted])

  // --- Build display grid: always overlay guesses on truth grid (like original) ---
  const displayGrid: Grid | null = (() => {
    if (!myPlayer) return null
    // Always show guesses overlaid, even when not in guess mode
    if (guessGrid.length > 0) {
      return myPlayer.grid.map((row, r) =>
        row.map((cell, c) => {
          const guessVal = guessGrid[r]?.[c]
          return guessVal && guessVal !== "?" ? (guessVal as any) : cell
        })
      ) as Grid
    }
    return myPlayer.grid
  })()

  // --- Render ---

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="h-12 w-12 animate-spin text-slate-600 mx-auto mb-4" />
          <p className="text-slate-600">Loading...</p>
        </div>
      </div>
    )
  }

  // ===================== PHASE 1: JOIN =====================
  if (phase === "join") {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle className="text-2xl flex items-center justify-center gap-2">
              <Users className="h-6 w-6" />
              Join Game
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {joinInfo ? (
              <>
                <div className="space-y-2">
                  <h3 className="font-semibold text-lg">
                    {joinInfo.name}
                  </h3>
                  {joinInfo.description && (
                    <p className="text-sm text-slate-600">
                      {joinInfo.description}
                    </p>
                  )}
                  <div className="flex items-center gap-2 text-sm text-slate-500">
                    <Users className="h-4 w-4" />
                    <span>
                      {joinInfo.participant_slots.filter(s => s.claimed).length}/
                      {joinInfo.participant_count} players joined
                    </span>
                  </div>
                </div>

                {joinInfo.participant_slots.some(s => s.claimed) && (
                  <div className="space-y-2">
                    <p className="text-sm font-medium text-slate-700">
                      Joined Players:
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {joinInfo.participant_slots.filter(s => s.claimed).map((s, i) => (
                        <Badge
                          key={i}
                          variant="secondary"
                          className="bg-blue-100 text-blue-700"
                        >
                          {s.player_name}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                <div className="space-y-3">
                  <Input
                    placeholder="Enter your name"
                    value={playerName}
                    onChange={(e) => setPlayerName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleJoin()
                    }}
                    disabled={joining}
                  />
                  <Button
                    className="w-full"
                    onClick={handleJoin}
                    disabled={joining || !playerName.trim()}
                  >
                    {joining ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <LogIn className="h-4 w-4 mr-2" />
                    )}
                    Join Game
                  </Button>
                </div>
              </>
            ) : (
              <div className="text-center text-slate-500">
                <AlertCircle className="h-8 w-8 mx-auto mb-2 text-red-400" />
                <p>
                  {error || "Unable to load game info. The link may be invalid or expired."}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    )
  }

  // ===================== PHASE 2: WAITING =====================
  if (phase === "waiting") {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle className="text-xl flex items-center justify-center gap-2">
              <Clock className="h-5 w-5" />
              Waiting for Players
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6 text-center">
            <div>
              <Loader2 className="h-10 w-10 animate-spin text-blue-500 mx-auto mb-3" />
              <p className="text-slate-600">
                Waiting for all players to join...
              </p>
            </div>

            {joinInfo && (
              <div className="space-y-3">
                <Progress
                  value={
                    (joinInfo.participant_slots.filter(s => s.claimed).length /
                      joinInfo.participant_count) *
                    100
                  }
                  className="h-3"
                />
                <p className="text-sm text-slate-500">
                  {joinInfo.participant_slots.filter(s => s.claimed).length}/
                  {joinInfo.participant_count} players joined
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {joinInfo.participant_slots.filter(s => s.claimed).map((s, i) => (
                    <Badge
                      key={i}
                      variant="secondary"
                      className={
                        s.player_name === session?.player_name
                          ? "bg-blue-200 text-blue-800 ring-2 ring-blue-400"
                          : "bg-slate-100"
                      }
                    >
                      {s.player_name}
                      {s.player_name === session?.player_name && " (You)"}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            <p className="text-xs text-slate-400">
              The game will start automatically when all players have joined.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  // ===================== PHASE 5: ALL COMPLETE =====================
  if (phase === "all_complete") {
    // Use scoreboard data (from DB) as the source of truth, not local completedGames
    const myScoreEntry = scoreboard.find(e => e.participant_index === session?.participant_index)
    const totalScore = myScoreEntry?.total_score ?? completedGames.reduce((sum, g) => sum + (g.score ?? 0), 0)
    // current_game is 1-indexed for display; subtract 1 to get completed count
    const gamesPlayed = myScoreEntry
      ? (myScoreEntry.is_finished ? myScoreEntry.total_games : myScoreEntry.current_game - 1)
      : completedGames.length
    const allDone = scoreboard.length > 0 && scoreboard.every(e => e.is_finished)
    const sortedBoard = [...scoreboard].sort((a, b) => b.total_score - a.total_score)
    const myRank = sortedBoard.findIndex(e => e.participant_index === session?.participant_index) + 1

    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
        <Card className="w-full max-w-lg">
          <CardHeader className="text-center">
            <CardTitle className="text-2xl flex items-center justify-center gap-2">
              <Trophy className="h-6 w-6 text-yellow-500" />
              {allDone ? "Final Results" : "Your Games Complete"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="text-center space-y-2">
              <p className="text-lg font-semibold">
                Well played, {session?.player_name}!
              </p>
              <div className="flex justify-center gap-6">
                <div className="text-center">
                  <p className="text-3xl font-bold text-blue-600">
                    {totalScore}
                  </p>
                  <p className="text-sm text-slate-500">Total Score</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-bold text-green-600">
                    {gamesPlayed}
                  </p>
                  <p className="text-sm text-slate-500">Games Played</p>
                </div>
                {myRank > 0 && allDone && (
                  <div className="text-center">
                    <p className="text-3xl font-bold text-yellow-600">
                      #{myRank}
                    </p>
                    <p className="text-sm text-slate-500">Rank</p>
                  </div>
                )}
              </div>
            </div>

            {/* Waiting for others */}
            {!allDone && scoreboard.length > 0 && (
              <div className="text-center p-4 bg-amber-50 border border-amber-200 rounded-lg space-y-2">
                <Loader2 className="h-6 w-6 animate-spin text-amber-500 mx-auto" />
                <p className="text-sm text-amber-700 font-medium">
                  Waiting for other players to finish...
                </p>
                <div className="text-xs text-amber-600 space-y-1">
                  {scoreboard.filter(e => !e.is_finished).map((e) => (
                    <p key={e.participant_index}>
                      {e.player_name} — Game {e.current_game}/{e.total_games}
                    </p>
                  ))}
                </div>
              </div>
            )}

            {/* Scoreboard */}
            {scoreboard.length > 0 && (
              <div className="space-y-2">
                <h3 className="font-semibold text-sm text-slate-700">
                  {allDone ? "Final Scoreboard" : "Live Scoreboard"}
                </h3>
                <div className="rounded-lg border overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium text-slate-600">
                          Rank
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-slate-600">
                          Player
                        </th>
                        <th className="px-3 py-2 text-center font-medium text-slate-600">
                          Progress
                        </th>
                        <th className="px-3 py-2 text-right font-medium text-slate-600">
                          Score
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedBoard.map((entry, idx) => (
                          <tr
                            key={entry.participant_index}
                            className={
                              entry.participant_index ===
                              session?.participant_index
                                ? "bg-blue-50 font-semibold"
                                : idx % 2 === 0
                                  ? "bg-white"
                                  : "bg-slate-50"
                            }
                          >
                            <td className="px-3 py-2">#{idx + 1}</td>
                            <td className="px-3 py-2">
                              {entry.player_name}
                              {entry.participant_index ===
                                session?.participant_index && (
                                <Badge
                                  variant="secondary"
                                  className="ml-2 text-xs bg-blue-100 text-blue-700"
                                >
                                  You
                                </Badge>
                              )}
                            </td>
                            <td className="px-3 py-2 text-center text-xs">
                              {entry.is_finished ? (
                                <Badge variant="secondary" className="bg-green-100 text-green-700 text-xs">Done</Badge>
                              ) : (
                                <span>{entry.current_game}/{entry.total_games}</span>
                              )}
                            </td>
                            <td className="px-3 py-2 text-right">
                              {entry.total_score}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    )
  }

  // ===================== PHASE 3 & 4: PLAYING / GAME RESULTS =====================

  const gridSize = gameState?.gameConfig?.baseSettings?.gridSize || 6
  const numSymbols = gameState?.gameConfig?.baseSettings?.numSymbols || 5
  const symbolsInUse = ALL_SYMBOLS.slice(0, numSymbols)
  const isMyTurn =
    myPlayer &&
    !myPlayer.isFinished &&
    myPlayer.isWaitingForHuman
  const isGameOver = myPlayer?.isFinished ?? false

  return (
    <div className="min-h-screen bg-slate-100">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">
              {joinInfo?.name || "Pattern Game"}
            </h1>
            <p className="text-sm text-slate-500">
              Playing as{" "}
              <span className="font-medium text-blue-600">
                {session?.player_name}
              </span>
              {gameState && (
                <span className="ml-2">
                  - Game {gameState.gameIndex + 1}
                </span>
              )}
            </p>
          </div>
          {gameState && (
            <Badge
              variant={isGameOver ? "secondary" : "default"}
              className={isGameOver ? "" : "animate-pulse"}
            >
              {isGameOver ? (
                <>
                  <CheckCircle className="h-3 w-3 mr-1" /> Finished
                </>
              ) : isMyTurn ? (
                <>
                  <Eye className="h-3 w-3 mr-1" /> Your Turn
                </>
              ) : (
                <>
                  <RefreshCw className="h-3 w-3 mr-1 animate-spin" />{" "}
                  Waiting...
                </>
              )}
            </Badge>
          )}
        </div>
      </div>

      <div className="max-w-5xl mx-auto p-4 space-y-4">
        {/* Live Scoreboard */}
        {scoreboard.length > 0 && (
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
                      <th className="px-3 py-1.5 text-left font-medium text-slate-600">
                        #
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-slate-600">
                        Player
                      </th>
                      <th className="px-3 py-1.5 text-center font-medium text-slate-600">
                        Game
                      </th>
                      <th className="px-3 py-1.5 text-right font-medium text-slate-600">
                        Score
                      </th>
                      <th className="px-3 py-1.5 text-center font-medium text-slate-600">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {scoreboard
                      .sort((a, b) => b.total_score - a.total_score)
                      .map((entry, idx) => (
                        <tr
                          key={entry.participant_index}
                          className={
                            entry.participant_index ===
                            session?.participant_index
                              ? "bg-blue-50 font-semibold"
                              : idx % 2 === 0
                                ? "bg-white"
                                : "bg-slate-50"
                          }
                        >
                          <td className="px-3 py-1.5">{idx + 1}</td>
                          <td className="px-3 py-1.5">
                            {entry.player_name}
                            {entry.participant_index ===
                              session?.participant_index && (
                              <span className="text-blue-500 ml-1">
                                (You)
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            {entry.current_game}/{entry.total_games}
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            {entry.total_score}
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            <Badge
                              variant={
                                entry.is_finished
                                  ? "secondary"
                                  : "default"
                              }
                              className="text-xs"
                            >
                              {entry.status || (entry.is_finished ? "Done" : "Playing")}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Game Area */}
        {gameState && myPlayer && displayGrid ? (
          <div className="flex flex-col md:flex-row gap-4">
            {/* Left: Game Board */}
            <div className="flex-shrink-0">
              <Card className="p-4">
                <GameBoard
                  grid={displayGrid}
                  onCellClick={isMyTurn ? handleCellClick : () => {}}
                  selectedCells={
                    isMyTurn && !isGuessMode ? selectedCells : []
                  }
                  queriedCells={myPlayer.queriedCells}
                  isGuessing={!!(isGuessMode && isMyTurn)}
                  finalGuess={myPlayer.finalGuess}
                  masterPattern={
                    gameState.masterPattern ||
                    (Array.from({ length: gridSize }, () =>
                      Array(gridSize).fill(null)
                    ) as Grid)
                  }
                  isGameOver={isGameOver}
                  gridSize={gridSize}
                  symbolsInUse={symbolsInUse}
                  readOnly={!isMyTurn}
                />
              </Card>
            </div>

            {/* Right: Controls + Log */}
            <div className="flex-grow space-y-4">
              {/* Score display */}
              {myPlayer.score !== null && (
                <Card className="p-4 bg-blue-50">
                  <p className="text-center text-blue-700 font-semibold text-lg">
                    Score: {myPlayer.score}
                  </p>
                </Card>
              )}

              {/* Phase 4: Game Results overlay */}
              {isGameOver && phase === "game_results" && (
                <Card className="p-4 bg-green-50 border-green-200 space-y-3">
                  <div className="text-center">
                    <CheckCircle className="h-8 w-8 text-green-500 mx-auto mb-2" />
                    <p className="font-semibold text-green-700">
                      Game {gameState.gameIndex + 1} Complete!
                    </p>
                    {myPlayer.score !== null && (
                      <p className="text-green-600">
                        Your score: {myPlayer.score}
                      </p>
                    )}
                  </div>
                  {(() => {
                    // Check if this is the last game for this player
                    const myScoreEntry = scoreboard.find(e => e.participant_index === session?.participant_index)
                    const totalGamesForMe = myScoreEntry?.total_games || joinInfo?.total_games || 1
                    const isLastGame = gameState.gameIndex + 1 >= totalGamesForMe

                    if (isLastGame) {
                      return (
                        <div className="text-center space-y-2">
                          <p className="text-sm text-green-600 font-medium">
                            You have completed all games!
                          </p>
                          <Button
                            className="w-full"
                            variant="outline"
                            onClick={() => {
                              // Track final game (deduplicate by gameIndex)
                              if (gameState && myPlayer) {
                                setCompletedGames((prev) => {
                                  if (prev.some(g => g.gameIndex === gameState.gameIndex)) return prev
                                  return [...prev, { gameIndex: gameState.gameIndex, score: myPlayer.score }]
                                })
                              }
                              setPhase("all_complete")
                            }}
                          >
                            View Final Results
                          </Button>
                        </div>
                      )
                    }

                    return (
                      <Button
                        className="w-full"
                        onClick={handleNextGame}
                        disabled={readySubmitted}
                      >
                        {readySubmitted ? (
                          <>
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                            Waiting for next game...
                          </>
                        ) : (
                          <>
                            Next Game
                            <RefreshCw className="h-4 w-4 ml-2" />
                          </>
                        )}
                      </Button>
                    )
                  })()}
                </Card>
              )}

              {/* Human player controls */}
              {isMyTurn && (
                <Card className="p-4 bg-blue-50 space-y-3">
                  {!isGuessMode ? (
                    <>
                      <p className="text-sm text-blue-700 font-medium">
                        Turn {myPlayer.turnNumber} - Select up to 3 cells to
                        observe ({selectedCells.length}/3 selected)
                      </p>
                      <div className="flex flex-col gap-2">
                        <Button
                          size="sm"
                          onClick={handleObserve}
                          disabled={
                            selectedCells.length === 0 || submitting
                          }
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
                          onClick={handleGiveUp}
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
                          onClick={handleGuess}
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

              {/* Waiting indicator when not your turn and game is active */}
              {!isMyTurn && !isGameOver && myPlayer && (
                <Card className="p-4 bg-slate-50">
                  <div className="flex items-center justify-center gap-2 text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span className="text-sm">
                      Turn {myPlayer.turnNumber} - Processing...
                    </span>
                  </div>
                </Card>
              )}

              {/* Game Log */}
              <GameLog log={myPlayer.log} />
            </div>
          </div>
        ) : (
          /* No active game state yet */
          <Card className="p-8">
            <div className="text-center text-slate-500">
              <Loader2 className="h-8 w-8 animate-spin mx-auto mb-3" />
              <p>Waiting for game data...</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
