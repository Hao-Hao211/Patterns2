"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Loader2, Database } from "lucide-react"

interface GameHistoryEntry {
  game_id: string
  grid_size: number
  num_symbols: number
  final_score: number
  observation_count: number
  participant_role: string
  test_set_id: string | null
  created_at: string | null
}

interface GameHistorySelectorProps {
  modelName: string
  selectedGameIds: string[]
  onSelectionChange: (ids: string[]) => void
}

export function GameHistorySelector({
  modelName,
  selectedGameIds,
  onSelectionChange,
}: GameHistorySelectorProps) {
  const [games, setGames] = useState<GameHistoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!modelName || !expanded) return

    const fetchHistory = async () => {
      setLoading(true)
      setError(null)
      try {
        const response = await fetch(
          `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/games/history-for-evolving?model_name=${encodeURIComponent(modelName)}&limit=100`
        )
        if (!response.ok) throw new Error("Failed to fetch game history")
        const data: GameHistoryEntry[] = await response.json()
        setGames(data)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch history")
      } finally {
        setLoading(false)
      }
    }

    fetchHistory()
  }, [modelName, expanded])

  const toggleGame = (gameId: string) => {
    if (selectedGameIds.includes(gameId)) {
      onSelectionChange(selectedGameIds.filter((id) => id !== gameId))
    } else {
      onSelectionChange([...selectedGameIds, gameId])
    }
  }

  const selectAll = () => {
    onSelectionChange(games.map((g) => g.game_id))
  }

  const clearAll = () => {
    onSelectionChange([])
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-medium">Import Game History</Label>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setExpanded(!expanded)}
        >
          <Database className="mr-2 h-3 w-3" />
          {expanded ? "Hide" : "Browse"} Games ({selectedGameIds.length} selected)
        </Button>
      </div>

      {expanded && (
        <div className="border rounded-md p-3 space-y-2 max-h-60 overflow-y-auto bg-slate-50">
          {loading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              <span className="text-sm text-slate-500">Loading game history...</span>
            </div>
          ) : error ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : games.length === 0 ? (
            <p className="text-sm text-slate-500 text-center py-4">
              No game history found for {modelName}
            </p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-2">
                <Button variant="ghost" size="sm" onClick={selectAll} className="text-xs h-6">
                  Select All
                </Button>
                <Button variant="ghost" size="sm" onClick={clearAll} className="text-xs h-6">
                  Clear All
                </Button>
                <span className="text-xs text-slate-500 ml-auto">{games.length} games available</span>
              </div>
              {games.map((game) => (
                <div
                  key={game.game_id}
                  className="flex items-center gap-3 p-2 rounded hover:bg-slate-100 cursor-pointer"
                  onClick={() => toggleGame(game.game_id)}
                >
                  <Checkbox
                    checked={selectedGameIds.includes(game.game_id)}
                    onCheckedChange={() => toggleGame(game.game_id)}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-xs text-slate-500">
                        {game.game_id.substring(0, 8)}...
                      </span>
                      <span className="text-slate-700">
                        {game.grid_size}x{game.grid_size} grid
                      </span>
                      <span className="text-slate-500">|</span>
                      <span className={`font-medium ${game.final_score > 0 ? "text-green-600" : "text-red-500"}`}>
                        Score: {game.final_score}
                      </span>
                      <span className="text-slate-500">|</span>
                      <span className="text-slate-500">
                        {game.observation_count} obs
                      </span>
                    </div>
                    {game.created_at && (
                      <div className="text-xs text-slate-400">
                        {new Date(game.created_at).toLocaleDateString()} {new Date(game.created_at).toLocaleTimeString()}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
