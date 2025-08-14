"use client"

import { useState } from "react"
import Link from "next/link"
import { WelcomeScreen } from "@/components/welcome-screen"
import { GameSetupWizard, type FullGameConfig, type DesignerConfig } from "@/components/game-setup-wizard"
import { PlayingArea, type PlayerState } from "@/components/playing-area"
import { DesignerDashboard, type PlayerScoreInfo } from "@/components/designer-dashboard"
import type { Grid, Symbol } from "@/types/game-types"
import type { Position } from "@/types/game-types"
import { Button } from "@/components/ui/button"
import { History } from "lucide-react"

const ALL_SYMBOLS: Symbol[] = ["○", "△", "✖", "□", "★", "+"]

export type { Grid, Symbol, Position }

export default function GamePage() {
  type GamePhase = "welcome" | "setup" | "playing" | "results"
  const [currentPhase, setCurrentPhase] = useState<GamePhase>("welcome")
  const [fullGameConfig, setFullGameConfig] = useState<FullGameConfig | null>(null)
  const [masterPattern, setMasterPattern] = useState<Grid | null>(null)
  const [finalPlayerScores, setFinalPlayerScores] = useState<PlayerScoreInfo[]>([])

  const generateHumanDesignedPattern = (config: FullGameConfig): Grid => {
    const { gridSize, numSymbols } = config.baseSettings
    const designer: DesignerConfig = config.designer

    const currentSymbols = ALL_SYMBOLS.slice(0, numSymbols)
    const newPattern: Grid = Array(gridSize)
      .fill(null)
      .map(() => Array(gridSize).fill(null))

    const getRandomSymbol = () => currentSymbols[Math.floor(Math.random() * currentSymbols.length)]

    console.log("[generateHumanDesignedPattern] Generating pattern with config:", {
      gridSize,
      numSymbols,
      patternMode: designer.patternMode,
      symmetryType: designer.symmetryType,
      shiftStep: designer.shiftStep,
    })

    if (designer.patternMode === "Visual" && designer.symmetryType) {
      console.log("[generateHumanDesignedPattern] Entering Visual mode with symmetry:", designer.symmetryType)
      const symType = designer.symmetryType
      const n = gridSize

      if (symType === "Left-Right") {
        const halfCols = Math.floor(n / 2)
        for (let r = 0; r < n; r++) {
          const leftHalf: Symbol[] = []
          for (let c = 0; c < halfCols; c++) {
            leftHalf.push(getRandomSymbol())
          }
          const middleCol: Symbol[] = n % 2 === 1 ? [getRandomSymbol()] : []
          const rightHalf = [...leftHalf].reverse()
          newPattern[r] = [...leftHalf, ...middleCol, ...rightHalf]
        }
      } else if (symType === "Top-Bottom") {
        const halfRows = Math.floor(n / 2)
        for (let r = 0; r < halfRows; r++) {
          for (let c = 0; c < n; c++) {
            newPattern[r][c] = getRandomSymbol()
          }
        }
        if (n % 2 === 1) {
          for (let c = 0; c < n; c++) {
            newPattern[halfRows][c] = getRandomSymbol()
          }
        }
        for (let r = 0; r < halfRows; r++) {
          newPattern[n - 1 - r] = [...newPattern[r]]
        }
      } else if (symType === "Both") {
        const halfN = Math.floor(n / 2)
        for (let r = 0; r < halfN; r++) {
          const leftHalfCols: Symbol[] = []
          for (let c = 0; c < halfN; c++) {
            leftHalfCols.push(getRandomSymbol())
          }
          const middleCol: Symbol[] = n % 2 === 1 ? [getRandomSymbol()] : []
          const rightHalfCols = [...leftHalfCols].reverse()
          newPattern[r] = [...leftHalfCols, ...middleCol, ...rightHalfCols]
        }
        if (n % 2 === 1) {
          const r = halfN
          const leftHalfCols: Symbol[] = []
          for (let c = 0; c < halfN; c++) {
            leftHalfCols.push(getRandomSymbol())
          }
          const middleCol: Symbol[] = n % 2 === 1 ? [getRandomSymbol()] : []
          const rightHalfCols = [...leftHalfCols].reverse()
          newPattern[r] = [...leftHalfCols, ...middleCol, ...rightHalfCols]
        }
        for (let r = 0; r < halfN; r++) {
          newPattern[n - 1 - r] = [...newPattern[r]]
        }
      }
    } else if (designer.patternMode === "Algorithmic" && designer.shiftStep !== undefined) {
      console.log("[generateHumanDesignedPattern] Entering Algorithmic mode with shiftStep:", designer.shiftStep)
      const step = designer.shiftStep
      const m = currentSymbols.length // Number of unique symbols
      const gs = gridSize // Grid size

      if (m === 0) {
        console.error("[generateHumanDesignedPattern] No symbols available for Algorithmic mode!")
        for (let r = 0; r < gs; r++) {
          for (let c = 0; c < gs; c++) {
            newPattern[r][c] = ALL_SYMBOLS[0]
          }
        }
        return newPattern
      }

      // Generate first row: S0, S1, ..., Sm-1, S0, S1, ...
      for (let c = 0; c < gs; c++) {
        newPattern[0][c] = currentSymbols[c % m]
      }

      // Generate subsequent rows by cyclically shifting the previous row
      for (let r = 1; r < gs; r++) {
        for (let c = 0; c < gs; c++) {
          // Calculate the source column index in the previous row
          // (c + step) gives a left shift by 'step'
          // % gs ensures the index wraps around the grid width
          const sourceCol = (c + step) % gs
          newPattern[r][c] = newPattern[r - 1][sourceCol]
        }
      }
    } else {
      console.warn(
        "[generateHumanDesignedPattern] Pattern mode not fully specified or unknown, defaulting to Random. Mode:",
        designer.patternMode,
        "Symmetry:",
        designer.symmetryType,
        "Shift:",
        designer.shiftStep,
      )
      for (let r = 0; r < gridSize; r++) {
        for (let c = 0; c < gridSize; c++) {
          newPattern[r][c] = getRandomSymbol()
        }
      }
    }
    console.log("[generateHumanDesignedPattern] Generated pattern:", newPattern.map((row) => row.join(" ")).join("\n"))
    return newPattern
  }

  const handleStartSetup = () => {
    setCurrentPhase("setup")
  }

  const handleSetupComplete = async (config: FullGameConfig) => {
    setFullGameConfig(config)
    let finalMasterPattern: Grid | null = null

    if (config.designer.type === "Human") {
      if (config.designer.patternMode === "Custom" && config.designer.customPattern) {
        finalMasterPattern = config.designer.customPattern
      } else if (config.designer.patternMode !== "Custom") {
        finalMasterPattern = generateHumanDesignedPattern(config)
      }
    } else if (config.designer.type === "LLM" && config.designer.llmDesignedPattern) {
      finalMasterPattern = config.designer.llmDesignedPattern
    }

    if (finalMasterPattern) {
      setMasterPattern(finalMasterPattern)
      setCurrentPhase("playing")
      setFinalPlayerScores([])
    } else {
      console.error("Master pattern could not be determined from setup. Config:", config)
      alert("Error: Master pattern could not be set up. Please check configuration or try again.")
    }
  }

  const handleGameEnd = async (
    playerScoresFromGame: Array<{ playerId: string; score: number }>,
    playerStatesFromGame: Record<string, PlayerState>,
  ) => {
    if (!fullGameConfig || !masterPattern) return

    const enrichedScores: PlayerScoreInfo[] = playerScoresFromGame.map((ps) => {
      const playerConfig = fullGameConfig.players.find((p) => p.id === ps.playerId)
      return {
        id: ps.playerId,
        name: playerConfig?.name || "Unknown Player",
        score: ps.score,
      }
    })
    setFinalPlayerScores(enrichedScores)
    setCurrentPhase("results")

    const gameDataToSave = {
      grid_size: fullGameConfig.baseSettings.gridSize,
      num_symbols: fullGameConfig.baseSettings.numSymbols,
      designer_type: fullGameConfig.designer.type,
      designer_llm_model: fullGameConfig.designer.llmModel,
      designer_llm_model_params: fullGameConfig.designer.llmModelParams, // 新增
      designer_pattern_mode: fullGameConfig.designer.patternMode,
      master_pattern: masterPattern,
      game_config_dump: fullGameConfig,
      players: fullGameConfig.players.map((pConfig) => {
        const pState = playerStatesFromGame[pConfig.id]
        const pScore = playerScoresFromGame.find((ps) => ps.playerId === pConfig.id)
        return {
          player_name_in_game: pConfig.name,
          player_type: pConfig.type,
          player_llm_model: pConfig.llmModel,
          player_llm_model_params: pConfig.llmModelParams, // 新增
          final_score: pScore?.score ?? 0,
          final_guess: pState?.finalGuess,
          action_log: pState?.log,
          queried_cells:
            pState?.queriedCells?.map((pos) => ({
              row: pos.row,
              col: pos.col,
            })) || [],
        }
      }),
    }

    try {
      const backendUrl = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/games`
      const response = await fetch(backendUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(gameDataToSave),
      })

      if (!response.ok) {
        let errorDetail = "Failed to save game data due to server error."
        try {
          const errorData = await response.json()
          errorDetail = errorData.error || errorData.detail || errorDetail
        } catch (parseError) {
          console.error("Failed to parse error response:", parseError)
        }
        throw new Error(errorDetail)
      }
      const responseData = await response.json()
      console.log("Game data saved successfully. Game ID:", responseData.game_id)
    } catch (error) {
      console.error("Error saving game data:", error)
    }
  }

  const handlePlayAgain = () => {
    setFullGameConfig(null)
    setMasterPattern(null)
    setFinalPlayerScores([])
    setCurrentPhase("welcome")
  }

  if (currentPhase === "welcome") {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-black">
        <WelcomeScreen onStartSetup={handleStartSetup} />
        <div className="flex gap-4 mt-8">
          <Button variant="link" className="text-neutral-400 hover:text-neutral-200" asChild>
            <Link href="/history">
              <History className="mr-2 h-4 w-4" />
              View Game History
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  if (currentPhase === "setup") {
    return <GameSetupWizard onSetupComplete={handleSetupComplete} allSymbols={ALL_SYMBOLS} />
  }

  if (currentPhase === "playing" && fullGameConfig && masterPattern) {
    return (
      <PlayingArea
        gameConfig={fullGameConfig}
        masterPattern={masterPattern}
        onGameEnd={handleGameEnd}
        allSymbols={ALL_SYMBOLS}
      />
    )
  }

  if (currentPhase === "results" && fullGameConfig) {
    return (
      <DesignerDashboard
        designerType={fullGameConfig.designer.type}
        playerScores={finalPlayerScores}
        onPlayAgain={handlePlayAgain}
      />
    )
  }

  return <div className="flex items-center justify-center min-h-screen">Loading or unknown game state...</div>
}
