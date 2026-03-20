"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Award, Brain, Info } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import Link from "next/link"

export interface PlayerScoreInfo {
  id: string // player's original ID from config
  name: string
  score: number
}

interface DesignerDashboardProps {
  designerType: "Human" | "LLM"
  playerScores: PlayerScoreInfo[]
  onPlayAgain: () => void
}

export function DesignerDashboard({ designerType, playerScores, onPlayAgain }: DesignerDashboardProps) {
  let designerScore = 0
  let bestScore = Number.NEGATIVE_INFINITY
  let worstScore = Number.POSITIVE_INFINITY

  if (playerScores.length > 0) {
    playerScores.forEach((p) => {
      if (p.score > bestScore) bestScore = p.score
      if (p.score < worstScore) worstScore = p.score
    })

    // Designer Score = 2 × (best - worst). No clipping; negative scores are allowed.
    // Note: dropout penalty is not applied here (interactive mode, not saved to DB).
    if (bestScore === Number.NEGATIVE_INFINITY || worstScore === Number.POSITIVE_INFINITY || playerScores.length < 1) {
      designerScore = 0
    } else {
      designerScore = 2 * (bestScore - worstScore)
    }
  }

  return (
    <div className="min-h-screen bg-slate-100 flex flex-col items-center justify-center p-4">
      <Card className="w-full max-w-3xl">
        <CardHeader className="text-center">
          <Award className="h-16 w-16 text-yellow-500 mx-auto mb-4" />
          <CardTitle className="text-3xl font-bold">Game Over - Results</CardTitle>
          <CardDescription>Scores for all players and the designer ({designerType}).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <h3 className="text-xl font-semibold mb-2 text-center">Player Scores</h3>
            {playerScores.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Player Name</TableHead>
                    <TableHead className="text-right">Final Score</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {playerScores
                    .sort((a, b) => b.score - a.score) // Sort by score descending
                    .map((player) => (
                      <TableRow key={player.id}>
                        <TableCell>{player.name}</TableCell>
                        <TableCell className="text-right font-medium">{player.score}</TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            ) : (
              <p className="text-center text-slate-500">No player scores available.</p>
            )}
          </div>

          <Card className="bg-slate-800 text-white p-6 rounded-lg">
            <div className="flex items-center justify-center space-x-3 mb-3">
              <Brain className="h-8 w-8 text-sky-400" />
              <h3 className="text-2xl font-semibold text-center">Designer Score ({designerType})</h3>
            </div>
            <p className="text-5xl font-bold text-center text-sky-400">{designerScore}</p>
            <div className="text-xs text-slate-400 text-center mt-2 flex items-center justify-center">
              <p>Score = 2 × (Best Score − Worst Score)</p>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Info className="h-4 w-4 ml-2 cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="bg-black text-white p-2 rounded-md text-xs">
                    <p>Best Score: {bestScore === Number.NEGATIVE_INFINITY ? "N/A" : bestScore}</p>
                    <p>Worst Score: {worstScore === Number.POSITIVE_INFINITY ? "N/A" : worstScore}</p>
                    <p>
                      Formula: 2 × ({bestScore === Number.NEGATIVE_INFINITY ? "N/A" : bestScore} −{" "}
                      {worstScore === Number.POSITIVE_INFINITY ? "N/A" : worstScore}) = {designerScore}
                    </p>
                    <p className="mt-1 text-slate-500">Dropout penalty (−5 first, −10 each additional) applied when saving to DB.</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          </Card>
        </CardContent>
        <CardFooter className="pt-8 flex flex-col sm:flex-row gap-2">
          <Button size="lg" className="w-full sm:flex-1" onClick={onPlayAgain}>
            Play Again
          </Button>
          <Button size="lg" variant="outline" className="w-full sm:flex-1 bg-transparent" asChild>
            <Link href="/history">View Game History</Link>
          </Button>
        </CardFooter>
      </Card>
    </div>
  )
}
