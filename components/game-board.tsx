"use client"

import type { FC } from "react"
import React from "react" // 添加这行
import type { Grid, Position, Symbol as AppSymbol } from "@/types/game-types"
import { cn } from "@/lib/utils"

interface GameBoardProps {
  grid: Grid
  onCellClick: (row: number, col: number) => void
  selectedCells: Position[]
  queriedCells: Position[]
  isGuessing: boolean
  finalGuess: Grid | null
  masterPattern: Grid
  isGameOver: boolean
  gridSize: number
  symbolsInUse: AppSymbol[]
  readOnly?: boolean
}

const symbolMap: Record<AppSymbol, string> = {
  "○": "text-blue-500",
  "△": "text-green-500",
  "✖": "text-red-500",
  "□": "text-purple-500",
  "★": "text-yellow-500",
  "+": "text-orange-500",
}

export const GameBoard: FC<GameBoardProps> = ({
  grid,
  onCellClick,
  selectedCells,
  queriedCells,
  isGuessing,
  finalGuess,
  masterPattern,
  isGameOver,
  gridSize,
  symbolsInUse,
  readOnly = false,
}) => {
  const renderCellContent = (row: number, col: number) => {
    let displaySymbol: AppSymbol | "?" | null = null

    if (isGameOver && finalGuess) {
      const isCellQueried = queriedCells.some((p) => p.row === row && p.col === col)
      if (isCellQueried) {
        displaySymbol = masterPattern[row][col]
      } else {
        displaySymbol = finalGuess[row][col]
      }
    } else {
      displaySymbol = grid[row][col]
    }

    if (displaySymbol === "?") {
      return <span className="text-slate-500 text-2xl">?</span>
    }
    if (displaySymbol) {
      return <span className={cn("text-3xl font-bold", symbolMap[displaySymbol as AppSymbol])}>{displaySymbol}</span>
    }
    return null
  }

  return (
    // Removed w-min from this div
    <div className="p-2 bg-slate-200 rounded-lg inline-block">
      {" "}
      {/* Added inline-block for shrink-wrapping */}
      <div
        // Removed dynamic grid-cols-* class
        className="grid gap-1"
        style={{ gridTemplateColumns: `auto repeat(${gridSize}, minmax(0, 1fr))` }}
      >
        <div /> {/* Empty corner for alignment */}
        {/* Column Headers (A, B, C...) */}
        {Array.from({ length: gridSize }).map((_, i) => (
          <div
            key={`header-col-${i}`}
            className="w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center font-bold text-slate-600"
          >
            {String.fromCharCode(65 + i)}
          </div>
        ))}
        {/* Rows: Header + Cells */}
        {Array.from({ length: gridSize }).map((_, rowIndex) => (
          <React.Fragment key={`row-${rowIndex}`}>
            {/* Row Header (1, 2, 3...) */}
            <div
              key={`header-row-${rowIndex}`}
              className="w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center font-bold text-slate-600"
            >
              {rowIndex + 1}
            </div>
            {/* Cells for the current row */}
            {Array.from({ length: gridSize }).map((_, colIndex) => {
              const isSelected = !readOnly && selectedCells.some((p) => p.row === rowIndex && p.col === colIndex)
              const isQueried = queriedCells.some((p) => p.row === rowIndex && p.col === colIndex)

              let cellBgClass = "bg-white" // Default background

              if (isGameOver && finalGuess) {
                if (isQueried) {
                  cellBgClass = "bg-slate-300"
                } else {
                  const playerGuessedSymbol = finalGuess[rowIndex][colIndex]
                  const masterSymbol = masterPattern[rowIndex][colIndex]
                  if (
                    playerGuessedSymbol === masterSymbol &&
                    playerGuessedSymbol !== null &&
                    playerGuessedSymbol !== "?"
                  ) {
                    cellBgClass = "bg-green-300"
                  } else {
                    cellBgClass = "bg-red-300"
                  }
                }
              } else if (isQueried) {
                cellBgClass = "bg-slate-200"
              }

              const currentGridSymbol = grid && grid[rowIndex] ? grid[rowIndex][colIndex] : null

              return (
                <div
                  key={`cell-${rowIndex}-${colIndex}`}
                  onClick={() => !readOnly && onCellClick(rowIndex, colIndex)}
                  className={cn(
                    "w-10 h-10 sm:w-12 sm:h-12 rounded-md flex items-center justify-center relative transition-all",
                    cellBgClass,
                    !readOnly && !isGameOver && "hover:bg-slate-100 hover:shadow-md",
                    isGameOver || readOnly ? "cursor-not-allowed" : "cursor-pointer",
                    isSelected && !isGuessing && "ring-2 ring-blue-500 ring-offset-2",
                    isGuessing && !isQueried && !readOnly && !isGameOver && "hover:bg-blue-50",
                  )}
                  role="button"
                  aria-label={`Cell ${String.fromCharCode(65 + colIndex)}${rowIndex + 1}, symbol ${currentGridSymbol || "empty"}`}
                  tabIndex={readOnly ? -1 : 0}
                >
                  {renderCellContent(rowIndex, colIndex)}
                </div>
              )
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}
