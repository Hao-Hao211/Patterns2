"use client"

import { useState, useEffect, useCallback, useMemo, useRef } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { GameBoard } from "@/components/game-board"
import { Controls } from "@/components/controls"
import { GameLog } from "@/components/game-log"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { FullGameConfig, PlayerConfig as BasePlayerConfig } from "./game-setup-wizard"
import type { Grid, Symbol, Position } from "@/types/game-types"
import { Button } from "@/components/ui/button"
import { CheckCircle, Eye, Brain, Clock, Pause, Play } from "lucide-react"

const createInitialGrid = (size: number): Grid =>
  Array(size)
    .fill(null)
    .map(() => Array(size).fill("?"))

export interface PlayerState extends BasePlayerConfig {
  grid: Grid // The "truth" grid with observed cells and '?'
  guessGrid: Grid // The player's temporary guesses
  queriedCells: Position[]
  selectedCells: Position[]
  isGuessing: boolean // Final submission mode
  isGuessMode: boolean // Temporary guess mode
  log: string[]
  score: number | null
  isFinished: boolean
  finalGuess: Grid | null
  turnNumber: number
  isWaitingForLLM: boolean
  isPaused: boolean // 是否暂停自动执行
}

interface PlayingAreaProps {
  gameConfig: FullGameConfig
  masterPattern: Grid
  onGameEnd: (
    playerScores: Array<{ playerId: string; score: number }>,
    playerStates: Record<string, PlayerState>,
  ) => void
  allSymbols: Symbol[]
}

// LLM Player API 响应类型
interface LLMPlayerTurnResponse {
  action: "observe" | "guess" | "give_up"
  cellsToObserve?: Position[]
  guessGrid?: Grid
  reasoning: string
  confidence?: number
}

// 辅助函数：确保网格数据正确序列化
function serializeGrid(grid: Grid): string[][] {
  return grid.map((row) =>
    row.map((cell) => {
      if (cell === null || cell === undefined) {
        return "?"
      }
      return String(cell)
    }),
  )
}

export function PlayingArea({ gameConfig, masterPattern, onGameEnd, allSymbols }: PlayingAreaProps) {
  const symbolsInUse = useMemo(
    () => allSymbols.slice(0, gameConfig.baseSettings.numSymbols),
    [allSymbols, gameConfig.baseSettings.numSymbols],
  )

  // 生成唯一的游戏ID
  const gameId = useRef(crypto.randomUUID()).current

  const [playerStates, setPlayerStates] = useState<Record<string, PlayerState>>(() => {
    const initialStates: Record<string, PlayerState> = {}
    gameConfig.players.forEach((pConfig) => {
      const gridSize = gameConfig.baseSettings.gridSize
      initialStates[pConfig.id] = {
        ...pConfig,
        grid: createInitialGrid(gridSize),
        guessGrid: createInitialGrid(gridSize),
        queriedCells: [],
        selectedCells: [],
        isGuessing: false,
        isGuessMode: false,
        log: [`Game started for ${pConfig.name}.`],
        score: null,
        isFinished: false,
        finalGuess: null,
        turnNumber: 1,
        isWaitingForLLM: false,
        isPaused: false,
      }
    })
    return initialStates
  })

  const [activeTab, setActiveTab] = useState<string>(gameConfig.players[0]?.id || "")
  const [allPlayersFinished, setAllPlayersFinished] = useState(false)
  const [showMasterPattern, setShowMasterPattern] = useState(false)
  const [globalPause, setGlobalPause] = useState(false) // 全局暂停控制

  // 用于跟踪正在进行的LLM请求
  const activeRequests = useRef<Set<string>>(new Set())

  useEffect(() => {
    const allPlayerIds = Object.keys(playerStates)
    if (allPlayerIds.length === 0) return
    const allFinished = allPlayerIds.every((id) => playerStates[id]?.isFinished)
    if (allFinished) {
      setAllPlayersFinished(true)
    }
  }, [playerStates])

  // 改进的LLM Player处理逻辑 - 支持并发、chat history和模型参数
  const performLLMPlayerTurn = useCallback(
    async (playerId: string) => {
      const player = playerStates[playerId]
      if (
        !player ||
        player.isFinished ||
        player.type !== "LLM" ||
        player.isWaitingForLLM ||
        player.isPaused ||
        globalPause
      ) {
        return
      }

      // 防止重复请求
      if (activeRequests.current.has(playerId)) {
        return
      }

      activeRequests.current.add(playerId)

      // 设置等待状态
      setPlayerStates((prev) => ({
        ...prev,
        [playerId]: { ...prev[playerId], isWaitingForLLM: true },
      }))

      try {
        // 确保网格数据正确序列化
        const serializedGrid = serializeGrid(player.grid)

        console.log("发送给后端的数据:", {
          playerId: player.id,
          playerName: player.name,
          gameId: gameId,
          currentGrid: serializedGrid,
          gridSize: gameConfig.baseSettings.gridSize,
          symbolsInUse: symbolsInUse,
          llmModel: player.llmModel || "chatgpt-4o-latest",
          llmModelParams: player.llmModelParams,
          turnNumber: player.turnNumber,
        })

        const backendUrl = "http://127.0.0.1:8000/api/llm-player-turn"
        const response = await fetch(backendUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            playerId: player.id,
            playerName: player.name,
            gameId: gameId,
            currentGrid: serializedGrid,
            gridSize: gameConfig.baseSettings.gridSize,
            symbolsInUse: symbolsInUse,
            llmModel: player.llmModel || "chatgpt-4o-latest",
            llmModelParams: player.llmModelParams,
            turnNumber: player.turnNumber,
          }),
        })

        if (!response.ok) {
          let errorMessage = `API request failed with status ${response.status}`

          try {
            const errorText = await response.text()
            console.error("后端错误响应:", errorText)

            try {
              const errorData = JSON.parse(errorText)
              errorMessage = errorData.detail || errorData.error || errorMessage
            } catch (parseError) {
              console.error("无法解析错误响应为JSON:", parseError)
              errorMessage = errorText || errorMessage
            }
          } catch (textError) {
            console.error("无法读取错误响应文本:", textError)
          }

          throw new Error(errorMessage)
        }

        const llmResponse: LLMPlayerTurnResponse = await response.json()

        // 执行LLM的动作
        setPlayerStates((prevStates) => {
          const currentPlayer = prevStates[playerId]
          if (!currentPlayer) return prevStates

          let updatedPlayer = {
            ...currentPlayer,
            isWaitingForLLM: false,
            turnNumber: currentPlayer.turnNumber + 1,
          }
          const newLog = [...updatedPlayer.log]

          // 添加LLM推理日志
          newLog.push(`Turn ${currentPlayer.turnNumber}: ${llmResponse.reasoning}`)

          if (llmResponse.action === "observe" && llmResponse.cellsToObserve) {
            // 执行观察动作
            const newGrid = updatedPlayer.grid.map((r) => [...r])
            const observedCoords: string[] = []
            const newlyQueried: Position[] = []

            llmResponse.cellsToObserve.forEach(({ row, col }) => {
              if (
                row >= 0 &&
                row < gameConfig.baseSettings.gridSize &&
                col >= 0 &&
                col < gameConfig.baseSettings.gridSize &&
                newGrid[row][col] === "?"
              ) {
                newGrid[row][col] = masterPattern[row][col]
                observedCoords.push(`${String.fromCharCode(65 + col)}${row + 1}`)
                newlyQueried.push({ row, col })
              }
            })

            if (observedCoords.length > 0) {
              newLog.push(`Observed cells: ${observedCoords.join(", ")}`)
            } else {
              newLog.push("No valid cells to observe")
            }

            updatedPlayer = {
              ...updatedPlayer,
              grid: newGrid,
              queriedCells: [...updatedPlayer.queriedCells, ...newlyQueried],
              selectedCells: [],
            }
          } else if (llmResponse.action === "guess" && llmResponse.guessGrid) {
            // 执行最终猜测动作
            let score = 0
            for (let r = 0; r < gameConfig.baseSettings.gridSize; r++) {
              for (let c = 0; c < gameConfig.baseSettings.gridSize; c++) {
                const isQueried = updatedPlayer.queriedCells.some((p) => p.row === r && p.col === c)
                if (isQueried) continue

                if (llmResponse.guessGrid[r][c] === masterPattern[r][c]) {
                  score++
                } else {
                  score--
                }
              }
            }

            const confidenceText = llmResponse.confidence
              ? ` (Confidence: ${(llmResponse.confidence * 100).toFixed(1)}%)`
              : ""
            newLog.push(`Final guess submitted${confidenceText}. Score: ${score}`)

            updatedPlayer = {
              ...updatedPlayer,
              grid: llmResponse.guessGrid,
              finalGuess: llmResponse.guessGrid,
              score,
              isFinished: true,
            }
          } else if (llmResponse.action === "give_up") {
            // 执行放弃动作
            newLog.push("Gave up the game to avoid negative score. Final score: 0")
            updatedPlayer = {
              ...updatedPlayer,
              score: 0,
              isFinished: true,
            }
          }

          updatedPlayer.log = newLog
          return { ...prevStates, [playerId]: updatedPlayer }
        })
      } catch (error) {
        console.error(`Error during LLM player ${player.name}'s turn:`, error)

        // 处理错误
        setPlayerStates((prevStates) => {
          const current = prevStates[playerId]
          if (!current) return prevStates

          let errorMessage = "An unexpected error occurred with LLM."

          if (error instanceof TypeError && error.message.toLowerCase().includes("failed to fetch")) {
            errorMessage =
              "CRITICAL ERROR: Could not connect to the Python backend. Please ensure it's running at http://127.0.0.1:8000."
          } else if (error instanceof Error) {
            errorMessage = `Error: ${String(error.message)}`
          } else {
            errorMessage = `Error: ${JSON.stringify(error)}`
          }

          return {
            ...prevStates,
            [playerId]: {
              ...current,
              isWaitingForLLM: false,
              log: [...current.log, errorMessage],
            },
          }
        })
      } finally {
        activeRequests.current.delete(playerId)
      }
    },
    [playerStates, masterPattern, gameConfig, symbolsInUse, gameId, globalPause],
  )

  // 自动触发所有LLM玩家的回合 - 支持并发
  useEffect(() => {
    if (globalPause || allPlayersFinished) return

    const llmPlayers = Object.values(playerStates).filter(
      (player) => player.type === "LLM" && !player.isFinished && !player.isWaitingForLLM && !player.isPaused,
    )

    if (llmPlayers.length === 0) return

    // 为每个LLM玩家设置独立的定时器
    const timers: NodeJS.Timeout[] = []

    llmPlayers.forEach((player) => {
      // 为每个玩家添加不同的延迟，避免同时发送请求
      const delay = 1000 + Math.random() * 2000 // 1-3秒的随机延迟
      const timer = setTimeout(() => {
        performLLMPlayerTurn(player.id)
      }, delay)
      timers.push(timer)
    })

    return () => {
      timers.forEach((timer) => clearTimeout(timer))
    }
  }, [playerStates, performLLMPlayerTurn, globalPause, allPlayersFinished])

  const handlePlayerAction = useCallback(
    (
      playerId: string,
      action: "observe" | "startGuessMode" | "endGuessMode" | "eraseGuesses" | "submitGuess" | "cellClick" | "reset",
      payload?: any,
    ) => {
      setPlayerStates((prevStates) => {
        const playerState = prevStates[playerId]
        if (!playerState || playerState.isFinished || playerState.type === "LLM") {
          return prevStates
        }

        const newState = { ...playerState }
        const newLog = [...newState.log]
        const gridSize = gameConfig.baseSettings.gridSize

        switch (action) {
          case "cellClick": {
            if (!payload) break
            const { row, col } = payload as Position

            if (newState.isGuessMode) {
              // Check if this cell has already been observed - if so, don't allow changes
              const isObservedCell = newState.queriedCells.some((p) => p.row === row && p.col === col)
              if (isObservedCell) {
                // Don't allow changes to observed cells during guess mode
                break
              }

              const newGuessGrid = newState.guessGrid.map((r) => [...r])
              const currentSymbol = newGuessGrid[row][col]
              const currentIndex = currentSymbol === "?" ? -1 : symbolsInUse.indexOf(currentSymbol as Symbol)
              const nextSymbolIndex = (currentIndex + 1) % symbolsInUse.length
              const newSymbol = symbolsInUse[nextSymbolIndex]
              newGuessGrid[row][col] = newSymbol
              newState.guessGrid = newGuessGrid

              const cellAddress = `${String.fromCharCode(65 + col)}${row + 1}`
              const newGuessLogEntry = `Guessed '${newSymbol}' at ${cellAddress}.`

              if (newLog.length > 0) {
                const lastLogEntry = newLog[newLog.length - 1]
                // Regex to check if the last log was a guess for the same cell
                // It looks for "Guessed 'X' at CELL_ADDRESS."
                const guessLogPattern = new RegExp(`^Guessed '.{1}' at ${cellAddress}\\.$`)
                if (guessLogPattern.test(lastLogEntry)) {
                  newLog[newLog.length - 1] = newGuessLogEntry // Update last entry
                } else {
                  newLog.push(newGuessLogEntry) // Add new entry
                }
              } else {
                newLog.push(newGuessLogEntry) // Add new entry if log is empty
              }
            } else {
              const isSelected = newState.selectedCells.some((p) => p.row === row && p.col === col)
              if (isSelected) {
                newState.selectedCells = newState.selectedCells.filter((p) => !(p.row === row && p.col === col))
              } else {
                // No limit on selection
                newState.selectedCells = [...newState.selectedCells, { row, col }]
              }
            }
            break
          }
          case "observe": {
            if (newState.selectedCells.length === 0) {
              newLog.push("No cells selected to observe.")
              break
            }
            const newGridObserve = newState.grid.map((r) => [...r])
            const newGuessGridObserve = newState.guessGrid.map((r) => [...r])
            const observedCoords: string[] = []
            const newlyQueriedFromObserve: Position[] = []

            newState.selectedCells.forEach(({ row, col }) => {
              const alreadyQueried = newState.queriedCells.some((p) => p.row === row && p.col === col)
              if (alreadyQueried) return

              const trueSymbol = masterPattern[row][col]
              newGridObserve[row][col] = trueSymbol
              newGuessGridObserve[row][col] = "?" // Clear any temporary guess in that cell
              observedCoords.push(`${String.fromCharCode(65 + col)}${row + 1}`)
              newlyQueriedFromObserve.push({ row, col })
            })

            if (observedCoords.length > 0) {
              newLog.push(`Observed: ${observedCoords.join(", ")}.`)
            } else {
              newLog.push("Selected cells were already known.")
            }
            newState.grid = newGridObserve
            newState.guessGrid = newGuessGridObserve
            newState.queriedCells = [...newState.queriedCells, ...newlyQueriedFromObserve]
            newState.selectedCells = []
            newState.turnNumber = newState.turnNumber + 1
            break
          }
          case "startGuessMode":
            newState.isGuessMode = true
            newState.selectedCells = [] // Clear selections when entering guess mode
            newLog.push("Entered guess mode.")
            break
          case "endGuessMode":
            newState.isGuessMode = false
            newLog.push("Exited guess mode.")
            break
          case "eraseGuesses":
            newState.guessGrid = createInitialGrid(gridSize)
            newLog.push("Erased all temporary guesses.")
            break
          case "submitGuess": {
            let score = 0
            // The final guess is the combination of the truth grid and the guess grid
            const finalGuessedGrid = newState.grid.map((row, r) =>
              row.map((cell, c) => {
                const guessCell = newState.guessGrid[r][c]
                return guessCell !== "?" ? guessCell : cell
              }),
            )
            newState.finalGuess = finalGuessedGrid

            for (let r = 0; r < gridSize; r++) {
              for (let c = 0; c < gridSize; c++) {
                const isQueried = newState.queriedCells.some((p) => p.row === r && p.col === c)
                if (isQueried) continue

                if (finalGuessedGrid[r][c] === masterPattern[r][c]) {
                  score++
                } else {
                  score--
                }
              }
            }
            newState.score = score
            newState.isFinished = true
            newLog.push(`Final guess submitted. Score: ${score}.`)
            break
          }
        }
        newState.log = newLog
        return { ...prevStates, [playerId]: newState }
      })
    },
    [symbolsInUse, masterPattern, gameConfig.baseSettings.gridSize],
  )

  const handleTogglePlayerPause = (playerId: string) => {
    setPlayerStates((prev) => ({
      ...prev,
      [playerId]: { ...prev[playerId], isPaused: !prev[playerId].isPaused },
    }))
  }

  const handleProceedToDashboard = () => {
    const scores = Object.keys(playerStates).map((id) => ({
      playerId: id,
      score: playerStates[id]?.score ?? 0,
    }))
    onGameEnd(scores, playerStates)
  }

  return (
    <div className="min-h-screen bg-slate-100 p-4 flex flex-col">
      <header className="text-center mb-6">
        <div className="flex items-center justify-center gap-4">
          <h1 className="text-4xl font-bold text-slate-800">Patterns II - Gameplay</h1>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setGlobalPause(!globalPause)}
            className="flex items-center gap-2"
          >
            {globalPause ? (
              <>
                <Play className="h-4 w-4" />
                Resume All LLM
              </>
            ) : (
              <>
                <Pause className="h-4 w-4" />
                Pause All LLM
              </>
            )}
          </Button>
        </div>
      </header>
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full flex flex-col flex-grow">
        <TabsList
          className="grid w-full gap-1"
          style={{ gridTemplateColumns: `repeat(${gameConfig.players.length}, minmax(0, 1fr))` }}
        >
          {gameConfig.players.map((player) => {
            const state = playerStates[player.id]
            let triggerClass = "data-[state=active]:bg-slate-200"
            let statusText = ""
            let statusIcon = null

            if (state?.isFinished) {
              triggerClass = "bg-green-200 text-green-800 data-[state=active]:bg-green-300"
              statusText = "(Done)"
              statusIcon = <CheckCircle className="h-4 w-4 ml-1" />
            } else if (state?.type === "LLM") {
              if (state.isPaused || globalPause) {
                triggerClass = "bg-gray-200 text-gray-600 data-[state=active]:bg-gray-300"
                statusText = "(Paused)"
                statusIcon = <Pause className="h-4 w-4 ml-1" />
              } else if (state.isWaitingForLLM) {
                triggerClass = "bg-orange-200 text-orange-800 data-[state=active]:bg-orange-300 animate-pulse"
                statusText = "(AI Thinking...)"
                statusIcon = <Brain className="h-4 w-4 ml-1 animate-spin" />
              } else {
                triggerClass = "bg-yellow-200 text-yellow-800 data-[state=active]:bg-yellow-300"
                statusText = `(Turn ${state.turnNumber})`
                statusIcon = <Clock className="h-4 w-4 ml-1" />
              }
            }

            let playerDisplayName = player.name
            if (state?.type === "LLM" && state?.llmModel) {
              playerDisplayName = `${player.name} [${state.llmModel}]`
            } else if (state?.type === "LLM") {
              playerDisplayName = `${player.name} [AI]`
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

        {gameConfig.players.map((player) => {
          const currentPlayerState = playerStates[player.id]
          if (!currentPlayerState) return null

          // Create a display grid that overlays guesses on top of the truth grid
          const displayGrid = currentPlayerState.grid.map((row, r) =>
            row.map((cell, c) => {
              const guessCell = currentPlayerState.guessGrid[r][c]
              return guessCell !== "?" ? guessCell : cell
            }),
          )

          return (
            <TabsContent key={player.id} value={player.id} className="flex-grow mt-2">
              <Card className="h-full flex flex-col">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    <span>
                      Player: {currentPlayerState.name}{" "}
                      {currentPlayerState.type === "LLM"
                        ? `[AI${currentPlayerState.llmModel ? ` - ${currentPlayerState.llmModel}` : ""}]`
                        : "[Human]"}
                    </span>
                    <div className="flex items-center gap-2">
                      {currentPlayerState.type === "LLM" && (
                        <>
                          <span className="text-sm text-slate-600">
                            Turn {currentPlayerState.turnNumber}
                            {currentPlayerState.isWaitingForLLM && (
                              <span className="ml-2 text-orange-600 animate-pulse">Thinking...</span>
                            )}
                          </span>
                          {!currentPlayerState.isFinished && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleTogglePlayerPause(player.id)}
                              className="flex items-center gap-1"
                            >
                              {currentPlayerState.isPaused ? (
                                <>
                                  <Play className="h-3 w-3" />
                                  Resume
                                </>
                              ) : (
                                <>
                                  <Pause className="h-3 w-3" />
                                  Pause
                                </>
                              )}
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex-grow flex flex-col md:flex-row items-start justify-center gap-8 p-4">
                  <div className="flex-shrink-0">
                    <GameBoard
                      grid={displayGrid} // Use the combined display grid
                      onCellClick={(row, col) => {
                        if (currentPlayerState.type === "Human" && !currentPlayerState.isFinished) {
                          handlePlayerAction(player.id, "cellClick", { row, col })
                        }
                      }}
                      selectedCells={currentPlayerState.selectedCells}
                      queriedCells={currentPlayerState.queriedCells}
                      isGuessing={currentPlayerState.isGuessing}
                      finalGuess={currentPlayerState.finalGuess}
                      masterPattern={masterPattern}
                      isGameOver={currentPlayerState.isFinished}
                      gridSize={gameConfig.baseSettings.gridSize}
                      symbolsInUse={symbolsInUse}
                      readOnly={currentPlayerState.type === "LLM" || currentPlayerState.isFinished}
                    />
                  </div>
                  <div className="flex-grow flex flex-col space-y-4 w-full md:max-w-xs">
                    {currentPlayerState.type === "Human" && !currentPlayerState.isFinished && (
                      <Controls
                        onObserve={() => handlePlayerAction(player.id, "observe")}
                        onStartGuessMode={() => handlePlayerAction(player.id, "startGuessMode")}
                        onEndGuessMode={() => handlePlayerAction(player.id, "endGuessMode")}
                        onEraseGuesses={() => handlePlayerAction(player.id, "eraseGuesses")}
                        onSubmitFinalGuess={() => handlePlayerAction(player.id, "submitGuess")}
                        isGuessing={currentPlayerState.isGuessing}
                        isGuessMode={currentPlayerState.isGuessMode}
                        canObserve={currentPlayerState.selectedCells.length > 0}
                        isGameOver={currentPlayerState.isFinished}
                      />
                    )}
                    {currentPlayerState.score !== null && (
                      <Card className="p-4 bg-blue-50">
                        <p className="text-center text-blue-700 font-semibold">Score: {currentPlayerState.score}</p>
                      </Card>
                    )}
                    <div className="flex-grow">
                      <GameLog log={currentPlayerState.log} />
                    </div>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          )
        })}
      </Tabs>
      {allPlayersFinished && (
        <div className="mt-6 p-4 bg-green-100 border border-green-300 rounded-lg">
          <h3 className="text-xl font-semibold text-green-800 mb-2 text-center">All players have finished!</h3>
          <p className="text-green-700 mb-4 text-center">You can review each player's final board above.</p>
          <div className="flex flex-col items-center gap-4">
            <Button onClick={() => setShowMasterPattern(!showMasterPattern)} variant="outline">
              <Eye className="mr-2 h-4 w-4" />
              {showMasterPattern ? "Hide" : "Show"} Correct Pattern
            </Button>
            {showMasterPattern && (
              <div className="mt-4 p-4 border rounded-md bg-white shadow-lg">
                <h4 className="text-lg font-semibold text-slate-700 mb-2 text-center">Master Pattern (Solution)</h4>
                <div className="flex justify-center">
                  <GameBoard
                    grid={masterPattern}
                    onCellClick={() => {}}
                    selectedCells={[]}
                    queriedCells={[]}
                    isGuessing={false}
                    finalGuess={null}
                    masterPattern={masterPattern}
                    isGameOver={true}
                    gridSize={gameConfig.baseSettings.gridSize}
                    symbolsInUse={symbolsInUse}
                    readOnly={true}
                  />
                </div>
              </div>
            )}
            <Button size="lg" onClick={handleProceedToDashboard} className="mt-4">
              <CheckCircle className="mr-2 h-5 w-5" />
              View Final Scores & Designer Dashboard
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
