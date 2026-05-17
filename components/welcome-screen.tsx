"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Puzzle, Brain, Zap, History, Settings, Trophy } from "lucide-react"
import Link from "next/link"

interface WelcomeScreenProps {
  onStartSetup: () => void
}

export function WelcomeScreen({ onStartSetup }: WelcomeScreenProps) {
  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-4 text-white">
      <Card className="w-full max-w-2xl bg-neutral-900 border-neutral-700 text-center shadow-2xl rounded-lg">
        <CardHeader className="pb-4">
          <div className="flex justify-center mb-6">
            <Puzzle className="h-20 w-20 text-neutral-300" />
          </div>
          <CardTitle className="text-5xl font-bold tracking-tight text-neutral-100">Patterns II</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6 text-neutral-300 px-8">
          <p className="text-lg">
            Challenge your abductive skills and unravel hidden patterns. Play as a scientist, or design the ultimate
            puzzle for others (even AI!) to solve.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4">
            <div className="flex items-start space-x-3 p-4 bg-neutral-800 rounded-md border border-neutral-700">
              <Brain className="h-8 w-8 text-neutral-400 mt-1 shrink-0" />
              <div>
                <h3 className="font-semibold text-neutral-100">Test Your Logic</h3>
                <p className="text-sm text-neutral-400">
                  Observe clues, form hypotheses, and deduce the master pattern.
                </p>
              </div>
            </div>
            <div className="flex items-start space-x-3 p-4 bg-neutral-800 rounded-md border border-neutral-700">
              <Zap className="h-8 w-8 text-neutral-400 mt-1 shrink-0" />
              <div>
                <h3 className="font-semibold text-neutral-100">Play or Design</h3>
                <p className="text-sm text-neutral-400">
                  Compete against humans or AI, or become the pattern designer.
                </p>
              </div>
            </div>
          </div>
        </CardContent>
        <CardFooter className="pt-8 pb-8 flex flex-col gap-4">
          <Button
            size="lg"
            variant="outline"
            className="w-full max-w-xs mx-auto border-neutral-500 hover:bg-neutral-800 hover:text-neutral-100 text-lg font-semibold shadow-lg transform hover:scale-105 transition-transform duration-150 text-neutral-300 bg-transparent"
            onClick={onStartSetup}
          >
            Start Game
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800"
            asChild
          >
            <Link href="/history">
              <History className="mr-2 h-4 w-4" />
              View Game History
            </Link>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800"
            asChild
          >
            <Link href="/test-sets">
              <Settings className="mr-2 h-4 w-4" />
              Setup Batch Test
            </Link>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800"
            asChild
          >
            <Link href="/leaderboard">
              <Trophy className="mr-2 h-4 w-4" />
              View Leaderboard
            </Link>
          </Button>
        </CardFooter>
      </Card>
      <p className="mt-8 text-xs text-white">
        By Hao Zhang
        <br />
      </p>
    </div>
  )
}
