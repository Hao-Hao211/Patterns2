"use client"

import { useState, type FC, useEffect } from "react"
import type { Grid, Symbol } from "@/types/game-types" // Changed import path
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { AlertCircle } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"

interface CustomPatternEditorProps {
  gridSize: number
  symbols: Symbol[]
  onSave: (pattern: Grid) => void
  onCancel: () => void
}

const symbolColorMap: Record<Symbol, string> = {
  "○": "text-blue-500",
  "△": "text-green-500",
  "✖": "text-red-500",
  "□": "text-purple-500",
  "★": "text-yellow-500",
  "+": "text-orange-500", // Added '+' symbol
}

export const CustomPatternEditor: FC<CustomPatternEditorProps> = ({ gridSize, symbols, onSave, onCancel }) => {
  const initialGrid = (): Grid =>
    Array(gridSize)
      .fill(null)
      .map(() => Array(gridSize).fill(symbols[0]))
  const [customGrid, setCustomGrid] = useState<Grid>(initialGrid())
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setCustomGrid(
      Array(gridSize)
        .fill(null)
        .map(() => Array(gridSize).fill(symbols[0])),
    )
  }, [gridSize, symbols])

  const handleCellClick = (row: number, col: number) => {
    setError(null)
    setCustomGrid((prevGrid) => {
      const newGrid = prevGrid.map((r) => [...r])
      const currentSymbol = newGrid[row][col]
      const currentIndex = symbols.indexOf(currentSymbol as Symbol) // Ensure symbols array is not empty
      const nextIndex = symbols.length > 0 ? (currentIndex + 1) % symbols.length : 0
      if (symbols.length > 0) {
        newGrid[row][col] = symbols[nextIndex]
      }
      return newGrid
    })
  }

  const handleSavePattern = () => {
    for (let r = 0; r < gridSize; r++) {
      for (let c = 0; c < gridSize; c++) {
        if (!customGrid[r][c] || customGrid[r][c] === "?") {
          setError(`Cell (${String.fromCharCode(65 + c)}${r + 1}) is not set. Please fill all cells.`)
          return
        }
      }
    }
    onSave(customGrid)
  }

  return (
    <Card className="w-full max-w-xl">
      <CardHeader>
        <CardTitle>Define Custom Pattern</CardTitle>
        <CardDescription>
          Click cells to cycle through symbols. Fill the entire {gridSize}x{gridSize} grid.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-center space-y-6">
        <div className="p-2 bg-slate-200 rounded-lg">
          <div
            className={cn("grid gap-1", `grid-cols-${gridSize + 1}`)}
            style={{ gridTemplateColumns: `auto repeat(${gridSize}, minmax(0, 1fr))` }}
          >
            <div />
            {Array.from({ length: gridSize }).map((_, i) => (
              <div
                key={`header-col-${i}`}
                className="w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center font-bold text-slate-600"
              >
                {String.fromCharCode(65 + i)}
              </div>
            ))}
            {customGrid.map((rowArray, rowIndex) => (
              <>
                <div
                  key={`header-row-${rowIndex}`}
                  className="w-10 h-10 sm:w-12 sm:h-12 flex items-center justify-center font-bold text-slate-600"
                >
                  {rowIndex + 1}
                </div>
                {rowArray.map((cell, colIndex) => (
                  <div
                    key={`${rowIndex}-${colIndex}`}
                    onClick={() => handleCellClick(rowIndex, colIndex)}
                    className={cn(
                      "w-10 h-10 sm:w-12 sm:h-12 bg-white rounded-md flex items-center justify-center cursor-pointer hover:bg-slate-100 transition-all",
                      "focus:ring-2 focus:ring-blue-500 focus:outline-none",
                    )}
                    tabIndex={0}
                    role="button"
                    aria-label={`Cell ${String.fromCharCode(65 + colIndex)}${rowIndex + 1}, current symbol ${cell}`}
                  >
                    {cell && cell !== "?" && (
                      <span className={cn("text-2xl sm:text-3xl font-bold", symbolColorMap[cell as Symbol])}>
                        {cell}
                      </span>
                    )}
                  </div>
                ))}
              </>
            ))}
          </div>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="flex space-x-4 w-full">
          <Button variant="outline" onClick={onCancel} className="flex-1">
            Cancel & Reset Game
          </Button>
          <Button onClick={handleSavePattern} className="flex-1">
            Save Pattern & Start Game
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
