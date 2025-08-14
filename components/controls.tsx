"use client"

import type { FC } from "react"
import { Button } from "@/components/ui/button"
import { Eye, Send, BrainCircuit, Eraser, ArrowLeft } from "lucide-react"

interface ControlsProps {
  onObserve: () => void
  onStartGuessMode: () => void
  onEndGuessMode: () => void
  onEraseGuesses: () => void
  onSubmitFinalGuess: () => void
  isGuessing: boolean // The final submission phase
  isGuessMode: boolean // The temporary guessing phase
  canObserve: boolean
  isGameOver: boolean
}

export const Controls: FC<ControlsProps> = ({
  onObserve,
  onStartGuessMode,
  onEndGuessMode,
  onEraseGuesses,
  onSubmitFinalGuess,
  isGuessing,
  isGuessMode,
  canObserve,
  isGameOver,
}) => {
  if (isGameOver) {
    return null // No controls needed once the game is finished for the player
  }

  if (isGuessing) {
    // Final submission phase
    return (
      <div className="flex flex-col space-y-2">
        <p className="text-center text-sm text-slate-600">Submit your final pattern.</p>
        <Button onClick={onSubmitFinalGuess} className="bg-green-600 hover:bg-green-700">
          <Send className="mr-2 h-4 w-4" />
          Submit Final Guess
        </Button>
      </div>
    )
  }

  if (isGuessMode) {
    // Temporary guessing phase
    return (
      <div className="flex flex-col space-y-2">
        <p className="text-center text-sm text-slate-600">Click cells to place your guesses.</p>
        <Button onClick={onEndGuessMode} variant="outline">
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Observe
        </Button>
        <Button onClick={onEraseGuesses} variant="destructive">
          <Eraser className="mr-2 h-4 w-4" />
          Erase All Guesses
        </Button>
        <Button onClick={onSubmitFinalGuess} className="mt-4 bg-green-600 hover:bg-green-700">
          <Send className="mr-2 h-4 w-4" />
          Submit Final Guess
        </Button>
      </div>
    )
  }

  // Default observation phase
  return (
    <div className="flex flex-col space-y-2">
      <Button onClick={onObserve} disabled={!canObserve}>
        <Eye className="mr-2 h-4 w-4" />
        Observe Selected Cells
      </Button>
      <Button onClick={onStartGuessMode} variant="outline">
        <BrainCircuit className="mr-2 h-4 w-4" />
        Guess
      </Button>
    </div>
  )
}
