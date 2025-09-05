"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { ArrowLeft, Plus, Play, Trophy, Clock, Users, GamepadIcon, RefreshCw, Eye, Trash2 } from "lucide-react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

interface TestSet {
  id: string
  name: string
  description?: string
  status: string
  total_games: number
  completed_games: number
  created_at: string
  config?: {
    participants: Array<{
      model_name: string
      model_params?: any
    }>
    llm_rotate_designer: boolean
    games: Array<{
      grid_size: number
      num_symbols: number
      repeat_count: number
    }>
  }
}

export default function TestSetsPage() {
  const [testSets, setTestSets] = useState<TestSet[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    fetchTestSets()
    // 设置自动刷新
    const interval = setInterval(fetchTestSets, 5000)
    return () => clearInterval(interval)
  }, [])

  const fetchTestSets = async () => {
    try {
      if (!refreshing) setLoading(true)
      setError(null)

      const response = await fetch("http://127.0.0.1:8000/api/test-sets")
      if (!response.ok) {
        throw new Error(`Failed to fetch test sets: ${response.status}`)
      }

      const data = await response.json()
      console.log("Fetched test sets:", data)
      setTestSets(data)
    } catch (err) {
      console.error("Error fetching test sets:", err)
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  const handleRefresh = () => {
    setRefreshing(true)
    fetchTestSets()
  }

  const handleStart = async (testSetId: string) => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/test-sets/${testSetId}/start`, {
        method: "POST",
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || "Failed to start test set")
      }

      // 刷新数据
      fetchTestSets()
    } catch (err) {
      console.error("Error starting test set:", err)
      setError(err instanceof Error ? err.message : "Failed to start test set")
    }
  }

  const handleDelete = async (testSetId: string) => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/test-sets/${testSetId}`, {
        method: "DELETE",
      })

      if (!response.ok) {
        throw new Error("Failed to delete test set")
      }

      // 刷新数据
      fetchTestSets()
    } catch (err) {
      console.error("Error deleting test set:", err)
      setError(err instanceof Error ? err.message : "Failed to delete test set")
    }
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "completed":
        return <Badge className="bg-green-500 hover:bg-green-600">completed</Badge>
      case "running":
        return <Badge className="bg-blue-500 hover:bg-blue-600">running</Badge>
      case "failed":
        return <Badge variant="destructive">failed</Badge>
      case "pending":
        return <Badge variant="secondary">pending</Badge>
      default:
        return <Badge variant="outline">{status}</Badge>
    }
  }

  const canStart = (testSet: TestSet) => {
    return testSet.status === "created" || testSet.status === "pending"
  }

  const canViewResults = (testSet: TestSet) => {
    return testSet.status === "completed" && testSet.completed_games > 0
  }

  if (loading && testSets.length === 0) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-slate-600 mx-auto mb-4"></div>
          <p className="text-slate-600">Loading test sets...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-100 p-4 sm:p-6 lg:p-8">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <header className="mb-8">
          <div className="flex items-center gap-4 mb-4">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/">
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to Game
              </Link>
            </Button>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold text-slate-800 mb-2">Leaderboard Test Sets</h1>
              <p className="text-slate-600">Manage and run competitive test sets for LLM evaluation</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={handleRefresh} disabled={refreshing}>
                <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
                Refresh
              </Button>
              <Button asChild>
                <Link href="/test-sets/create">
                  <Plus className="mr-2 h-4 w-4" />
                  Create New Test Set
                </Link>
              </Button>
            </div>
          </div>
        </header>

        {error && (
          <Card className="mb-6 border-red-200 bg-red-50">
            <CardContent className="pt-6">
              <p className="text-red-600">Error: {error}</p>
              <Button onClick={fetchTestSets} className="mt-2" size="sm">
                Try Again
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Test Sets List */}
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl font-semibold text-slate-800">Test Sets</h2>
            {testSets.length > 0 && <div className="text-sm text-slate-600">{testSets.length} test set(s)</div>}
          </div>

          {testSets.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <GamepadIcon className="h-12 w-12 text-slate-400 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-slate-600 mb-2">No test sets found</h3>
                <p className="text-slate-500 mb-6">Create your first test set to start evaluating LLM performance</p>
                <Button asChild>
                  <Link href="/test-sets/create">
                    <Plus className="mr-2 h-4 w-4" />
                    Create Test Set
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {testSets.map((testSet) => {
                const progressPercentage =
                  testSet.total_games > 0 ? (testSet.completed_games / testSet.total_games) * 100 : 0

                return (
                  <Card key={testSet.id} className="overflow-hidden">
                    <CardHeader className="pb-4">
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-3 mb-2">
                            <CardTitle className="text-xl">{testSet.name}</CardTitle>
                            {getStatusBadge(testSet.status)}
                          </div>
                          {testSet.description && (
                            <CardDescription className="mb-3">{testSet.description}</CardDescription>
                          )}
                          <div className="flex items-center gap-4 text-sm text-slate-600">
                            <div className="flex items-center gap-1">
                              <Clock className="h-4 w-4" />
                              Progress: {testSet.completed_games}/{testSet.total_games} games
                            </div>
                            {testSet.config && (
                              <>
                                <div className="flex items-center gap-1">
                                  <Users className="h-4 w-4" />
                                  {testSet.config.participants?.length || 0} participants
                                </div>
                                <div className="flex items-center gap-1">
                                  <GamepadIcon className="h-4 w-4" />
                                  {testSet.config.llm_rotate_designer ? "Multi-LLM" : "Fixed Designer"}
                                </div>
                              </>
                            )}
                            <div className="flex items-center gap-1">
                              <Clock className="h-4 w-4" />
                              Created: {new Date(testSet.created_at).toLocaleDateString()}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center gap-2 ml-4">
                          {canStart(testSet) && (
                            <Button onClick={() => handleStart(testSet.id)} size="sm">
                              <Play className="mr-2 h-4 w-4" />
                              Start
                            </Button>
                          )}

                          {testSet.status === "running" && (
                            <Button variant="outline" size="sm" asChild>
                              <Link href={`/test-sets/${testSet.id}/execute`}>
                                <Eye className="mr-2 h-4 w-4" />
                                View Progress
                              </Link>
                            </Button>
                          )}

                          {canViewResults(testSet) && (
                            <Button variant="outline" size="sm" asChild>
                              <Link href={`/leaderboard?test_set_id=${testSet.id}`}>
                                <Trophy className="mr-2 h-4 w-4" />
                                View Results
                              </Link>
                            </Button>
                          )}

                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button variant="ghost" size="sm">
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>Delete Test Set</AlertDialogTitle>
                                <AlertDialogDescription>
                                  Are you sure you want to delete "{testSet.name}"? This action cannot be undone and
                                  will also delete all associated game data.
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction onClick={() => handleDelete(testSet.id)}>Delete</AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        </div>
                      </div>
                    </CardHeader>

                    {testSet.total_games > 0 && (
                      <CardContent className="pt-0">
                        <div className="space-y-2">
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-slate-600">Progress</span>
                            <span className="font-medium">{progressPercentage.toFixed(1)}%</span>
                          </div>
                          <Progress value={progressPercentage} className="h-2" />
                        </div>
                      </CardContent>
                    )}
                  </Card>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
