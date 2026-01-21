"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Progress } from "@/components/ui/progress"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Separator } from "@/components/ui/separator"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { CustomPatternEditor } from "@/components/custom-pattern-editor"
import { GameBoard } from "@/components/game-board"
import type { Grid, Symbol } from "@/types/game-types"
import {
  ArrowLeft,
  ArrowRight,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Users,
  Settings,
  GamepadIcon,
  CheckCircle,
  Info,
  Loader2,
  AlertTriangle,
  Edit,
} from "lucide-react"

interface LLMModelParams {
  temperature?: number
  maxCompletionTokens?: number
  topP?: number
  frequencyPenalty?: number
  presencePenalty?: number
}

interface TestSetParticipant {
  model_name: string
  model_params?: LLMModelParams
}

interface TestSetGameConfig {
  grid_size: number
  num_symbols: number
  optional_prompt?: string
  custom_pattern?: Grid
  repeat_count: number
  pattern_mode?: "Visual" | "Algorithmic" | "Custom" | "Random" | "LLM" // Added "LLM" option
  symmetry_type?: "Left-Right" | "Top-Bottom" | "Both"
  shift_step?: number
  llm_pattern_model?: string
  llm_pattern_model_params?: LLMModelParams
  llm_pattern_prompt?: string
  llm_designed_pattern?: Grid
}

interface TestSetCreateRequest {
  name: string
  description?: string
  participants: TestSetParticipant[]
  llm_rotate_designer: boolean
  games: TestSetGameConfig[]
}

interface OpenAIModel {
  id: string
  object: string
  created: number
  owned_by: string
}

interface ModelsListResponse {
  object: string
  data: OpenAIModel[]
}

type SetupStep = "basic" | "settings" | "participants" | "games" | "customPatternEditor"

const allSymbols: Symbol[] = ["+", "○", "△", "□", "★", "✖"]

export default function CreateTestSetPage() {
  const router = useRouter()
  const [currentStep, setCurrentStep] = useState<SetupStep>("basic")
  const [editingGameIndex, setEditingGameIndex] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [availableModels, setAvailableModels] = useState<OpenAIModel[]>([])
  const [modelsLoading, setModelsLoading] = useState(true)
  const [modelsError, setModelsError] = useState<string | null>(null)

  const [isLLMDesigning, setIsLLMDesigning] = useState<Record<number, boolean>>({})
  const [llmDesignErrors, setLlmDesignErrors] = useState<Record<number, string | null>>({})

  // Form data
  const [formData, setFormData] = useState<TestSetCreateRequest>({
    name: "",
    description: "",
    participants: [{ model_name: "chatgpt-4o-latest" }],
    llm_rotate_designer: true,
    games: [
      {
        grid_size: 6,
        num_symbols: 5,
        repeat_count: 1,
      },
    ],
  })

  const stepOrder: SetupStep[] = ["basic", "settings", "participants", "games"]
  const stepTitles = ["Basic Info", "Settings", "Participants", "Games"]

  const getCurrentStepIndex = () => {
    if (currentStep === "customPatternEditor") return stepOrder.length
    return stepOrder.indexOf(currentStep)
  }

  // Fetch available models on component mount
  useEffect(() => {
    const fetchModels = async () => {
      try {
        setModelsLoading(true)
        setModelsError(null)

        const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/models`)
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }

        const data: ModelsListResponse = await response.json()
        setAvailableModels(data.data || [])

        // Update default model if available
        if (data.data && data.data.length > 0) {
          const defaultModel = data.data.find((m) => m.id === "chatgpt-4o-latest") || data.data[0]
          setFormData((prev) => ({
            ...prev,
            participants: prev.participants.map((p) => ({ ...p, model_name: defaultModel.id })),
          }))
        }
      } catch (error) {
        console.error("Failed to fetch models:", error)
        setModelsError("Failed to load available models. Using default list.")
        // Fallback to default models
        const defaultModels: OpenAIModel[] = [
          { id: "chatgpt-4o-latest", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4o", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4o-mini", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4-turbo", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-3.5-turbo", object: "model", created: 0, owned_by: "openai" },
        ]
        setAvailableModels(defaultModels)
      } finally {
        setModelsLoading(false)
      }
    }

    fetchModels()
  }, [])

  // Auto-adjust participants when designer rotation changes
  useEffect(() => {
    if (formData.llm_rotate_designer && formData.participants.length < 3) {
      const defaultModel = availableModels.length > 0 ? availableModels[0].id : "chatgpt-4o-latest"
      const newParticipants = [...formData.participants]
      while (newParticipants.length < 3) {
        newParticipants.push({ model_name: defaultModel })
      }
      setFormData((prev) => ({
        ...prev,
        participants: newParticipants,
      }))
    }
  }, [formData.llm_rotate_designer, availableModels])

  const handleNext = () => {
    const currentIndex = getCurrentStepIndex()
    if (currentIndex < stepOrder.length - 1) {
      setCurrentStep(stepOrder[currentIndex + 1])
    }
  }

  const handlePrevious = () => {
    const currentIndex = getCurrentStepIndex()
    if (currentIndex > 0) {
      setCurrentStep(stepOrder[currentIndex - 1])
    } else if (currentStep === "customPatternEditor") {
      setCurrentStep("games")
    }
  }

  const addParticipant = () => {
    const defaultModel = availableModels.length > 0 ? availableModels[0].id : "chatgpt-4o-latest"
    setFormData((prev) => ({
      ...prev,
      participants: [...prev.participants, { model_name: defaultModel }],
    }))
  }

  const removeParticipant = (index: number) => {
    const minParticipants = formData.llm_rotate_designer ? 3 : 1
    if (formData.participants.length > minParticipants) {
      setFormData((prev) => ({
        ...prev,
        participants: prev.participants.filter((_, i) => i !== index),
      }))
    }
  }

  const updateParticipant = (index: number, field: keyof TestSetParticipant, value: any) => {
    setFormData((prev) => ({
      ...prev,
      participants: prev.participants.map((p, i) => (i === index ? { ...p, [field]: value } : p)),
    }))
  }

  const updateParticipantParam = (participantIndex: number, param: keyof LLMModelParams, value: number | undefined) => {
    setFormData((prev) => ({
      ...prev,
      participants: prev.participants.map((p, i) => {
        if (i === participantIndex) {
          const newParams = { ...p.model_params }
          if (value === undefined || value === null) {
            delete newParams[param]
          } else {
            newParams[param] = value
          }
          return { ...p, model_params: Object.keys(newParams).length > 0 ? newParams : undefined }
        }
        return p
      }),
    }))
  }

  const addGame = () => {
    setFormData((prev) => ({
      ...prev,
      games: [
        ...prev.games,
        {
          grid_size: 6,
          num_symbols: 5,
          repeat_count: 1,
          pattern_mode: formData.llm_rotate_designer ? undefined : "Random",
        },
      ],
    }))
  }

  const removeGame = (index: number) => {
    if (formData.games.length > 1) {
      setFormData((prev) => ({
        ...prev,
        games: prev.games.filter((_, i) => i !== index),
      }))
    }
  }

  const updateGame = (index: number, field: keyof TestSetGameConfig, value: any) => {
    setFormData((prev) => ({
      ...prev,
      games: prev.games.map((g, i) => {
        if (i !== index) return g
        const updatedGame = { ...g, [field]: value }

        // Reset related fields when pattern_mode changes
        if (field === "pattern_mode") {
          if (value === "Visual") {
            updatedGame.symmetry_type = g.symmetry_type || "Left-Right"
            updatedGame.shift_step = undefined
            // Only clear custom_pattern if switching away from Custom mode
            if (g.pattern_mode === "Custom") {
              updatedGame.custom_pattern = undefined
            }
            if (g.pattern_mode === "LLM") {
              updatedGame.llm_pattern_model = undefined
              updatedGame.llm_pattern_model_params = undefined
              updatedGame.llm_pattern_prompt = undefined
              updatedGame.llm_designed_pattern = undefined
            }
          } else if (value === "Algorithmic") {
            updatedGame.shift_step = g.shift_step || 1
            updatedGame.symmetry_type = undefined
            // Only clear custom_pattern if switching away from Custom mode
            if (g.pattern_mode === "Custom") {
              updatedGame.custom_pattern = undefined
            }
            if (g.pattern_mode === "LLM") {
              updatedGame.llm_pattern_model = undefined
              updatedGame.llm_pattern_model_params = undefined
              updatedGame.llm_pattern_prompt = undefined
              updatedGame.llm_designed_pattern = undefined
            }
          } else if (value === "Custom") {
            // When switching to Custom mode, keep existing custom_pattern if available
            updatedGame.symmetry_type = undefined
            updatedGame.shift_step = undefined
            if (g.pattern_mode === "LLM") {
              updatedGame.llm_pattern_model = undefined
              updatedGame.llm_pattern_model_params = undefined
              updatedGame.llm_pattern_prompt = undefined
              updatedGame.llm_designed_pattern = undefined
            }
          } else if (value === "LLM") {
            updatedGame.symmetry_type = undefined
            updatedGame.shift_step = undefined
            updatedGame.custom_pattern = undefined
            updatedGame.llm_pattern_model = availableModels.length > 0 ? availableModels[0].id : "chatgpt-4o-latest"
            updatedGame.llm_pattern_model_params = undefined
            updatedGame.llm_pattern_prompt = undefined
            updatedGame.llm_designed_pattern = undefined
          } else {
            // For Random mode or others
            updatedGame.symmetry_type = undefined
            updatedGame.shift_step = undefined
            updatedGame.custom_pattern = undefined
            updatedGame.llm_pattern_model = undefined
            updatedGame.llm_pattern_model_params = undefined
            updatedGame.llm_pattern_prompt = undefined
            updatedGame.llm_designed_pattern = undefined
          }
        }

        return updatedGame
      }),
    }))
  }

  const updateGameLLMParam = (gameIndex: number, param: keyof LLMModelParams, value: number | undefined) => {
    setFormData((prev) => ({
      ...prev,
      games: prev.games.map((g, i) => {
        if (i === gameIndex) {
          const newParams = { ...g.llm_pattern_model_params }
          if (value === undefined || value === null) {
            delete newParams[param]
          } else {
            newParams[param] = value
          }
          return { ...g, llm_pattern_model_params: Object.keys(newParams).length > 0 ? newParams : undefined }
        }
        return g
      }),
    }))
  }

  const handleRequestLLMPattern = async (gameIndex: number) => {
    const game = formData.games[gameIndex]
    setIsLLMDesigning((prev) => ({ ...prev, [gameIndex]: true }))
    setLlmDesignErrors((prev) => ({ ...prev, [gameIndex]: null }))

    const backendUrl = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/design-pattern`

    try {
      const response = await fetch(backendUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gridSize: game.grid_size,
          numSymbols: game.num_symbols,
          llmModel: game.llm_pattern_model,
          llmModelParams: game.llm_pattern_model_params,
          prompt: game.llm_pattern_prompt,
        }),
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(
          errorData.error || errorData.detail || `LLM pattern design API request failed with status ${response.status}`,
        )
      }
      const data = await response.json()
      updateGame(gameIndex, "llm_designed_pattern", data.pattern as Grid)
    } catch (error) {
      console.error("Error designing pattern with LLM:", error)
      let detailedErrorMessage = "An unexpected error occurred."
      if (error instanceof TypeError && error.message.toLowerCase().includes("failed to fetch")) {
        detailedErrorMessage = `Network error: Could not connect to the backend server at ${backendUrl}. Please ensure it's running.`
      } else if (error instanceof Error) {
        detailedErrorMessage = error.message
      }
      setLlmDesignErrors((prev) => ({ ...prev, [gameIndex]: detailedErrorMessage }))
    } finally {
      setIsLLMDesigning((prev) => ({ ...prev, [gameIndex]: false }))
    }
  }

  const handleCustomPatternSave = (pattern: Grid) => {
    if (editingGameIndex !== null) {
      updateGame(editingGameIndex, "custom_pattern", pattern)
      setEditingGameIndex(null)
      setCurrentStep("games")
    }
  }

  const handleCustomPatternCancel = () => {
    setEditingGameIndex(null)
    setCurrentStep("games")
  }

  const calculateTotalGames = () => {
    return formData.games.reduce((total, game) => {
      const gameCount = formData.llm_rotate_designer
        ? formData.participants.length * game.repeat_count
        : game.repeat_count
      return total + gameCount
    }, 0)
  }

  const validateCurrentStep = () => {
    switch (currentStep) {
      case "basic":
        return formData.name.trim().length > 0
      case "settings":
        return true // Settings step is always valid
      case "participants":
        const minParticipants = formData.llm_rotate_designer ? 3 : 1
        return formData.participants.length >= minParticipants && formData.participants.every((p) => p.model_name)
      case "games":
        return (
          formData.games.length > 0 &&
          formData.games.every(
            (g) =>
              g.grid_size >= 3 &&
              g.grid_size <= 6 &&
              g.num_symbols >= 2 &&
              g.num_symbols <= 6 &&
              g.repeat_count >= 1 &&
              (formData.llm_rotate_designer || g.pattern_mode) &&
              (g.pattern_mode !== "Custom" || g.custom_pattern) &&
              (g.pattern_mode !== "LLM" || g.llm_designed_pattern),
          )
        )
      default:
        return false
    }
  }

  const handleSubmit = async () => {
    if (!validateCurrentStep()) {
      setError("Please fill in all required fields correctly")
      return
    }

    setLoading(true)
    setError(null)

    try {
      // Convert Grid to string[][] for API submission
      const apiFormData = {
        ...formData,
        games: formData.games.map((game) => ({
          ...game,
          custom_pattern: game.custom_pattern
            ? game.custom_pattern.map((row) => row.map((cell) => cell as string))
            : undefined,
          llm_designed_pattern: game.llm_designed_pattern
            ? game.llm_designed_pattern.map((row) => row.map((cell) => cell as string))
            : undefined,
        })),
      }

      // Debug log to verify custom patterns are included
      console.log("Submitting test set data:", JSON.stringify(apiFormData, null, 2))

      // Log specifically the custom patterns
      apiFormData.games.forEach((game, index) => {
        if (game.custom_pattern) {
          console.log(`Game ${index + 1} custom pattern:`, game.custom_pattern)
        }
      })

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(apiFormData),
      })

      if (response.ok) {
        const result = await response.json()
        console.log("Test set created successfully:", result)
        router.push("/test-sets")
      } else {
        const errorData = await response.json()
        console.error("Failed to create test set:", errorData)
        throw new Error(errorData.detail || "Failed to create test set")
      }
    } catch (err) {
      console.error("Error creating test set:", err)
      setError(err instanceof Error ? err.message : "Failed to create test set")
    } finally {
      setLoading(false)
    }
  }

  const renderStepContent = () => {
    if (currentStep === "customPatternEditor" && editingGameIndex !== null) {
      const game = formData.games[editingGameIndex]
      return (
        <CustomPatternEditor
          gridSize={game.grid_size}
          symbols={allSymbols.slice(0, game.num_symbols)}
          onSave={handleCustomPatternSave}
          onCancel={handleCustomPatternCancel}
          initialPattern={game.custom_pattern}
        />
      )
    }

    switch (currentStep) {
      case "basic":
        return (
          <div className="space-y-6">
            <div className="space-y-2">
              <Label htmlFor="name">Test Set Name *</Label>
              <Input
                id="name"
                value={formData.name}
                onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="Enter test set name"
                className="w-full"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="description">Description</Label>
              <Textarea
                id="description"
                value={formData.description}
                onChange={(e) => setFormData((prev) => ({ ...prev, description: e.target.value }))}
                placeholder="Optional description of this test set"
                rows={3}
              />
            </div>
          </div>
        )

      case "settings":
        return (
          <div className="space-y-6">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="space-y-1">
                  <Label>Designer Rotation</Label>
                  <p className="text-sm text-muted-foreground">
                    When enabled, each participant takes turns being the pattern designer
                  </p>
                </div>
                <Switch
                  checked={formData.llm_rotate_designer}
                  onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, llm_rotate_designer: checked }))}
                />
              </div>

              <Alert>
                <Info className="h-4 w-4" />
                <AlertDescription>
                  {formData.llm_rotate_designer
                    ? "Each participant will take turns designing patterns for other participants to solve. The designer scores 2×(highest_score - lowest_score) while other participants play as solvers. This creates a comprehensive evaluation where each model is tested both as a designer and solver."
                    : "Patterns will be generated randomly or using custom patterns you define in the games section. All participants will play as solvers only."}
                </AlertDescription>
              </Alert>

              {formData.llm_rotate_designer && (
                <Alert>
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    <strong>Designer Rotation Requirements:</strong>
                    <ul className="list-disc list-inside mt-2 space-y-1">
                      <li>Minimum 3 participants required (1 designer + 2 players per game)</li>
                      <li>The designer does not play in games they design</li>
                      <li>Designer scoring: 2 × (highest player score - lowest player score)</li>
                      <li>
                        Each participant will design {formData.games.reduce((sum, g) => sum + g.repeat_count, 0)} games
                      </li>
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
            </div>
          </div>
        )

      case "participants":
        const minParticipants = formData.llm_rotate_designer ? 3 : 1
        return (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-medium">LLM Participants</h3>
                <p className="text-sm text-muted-foreground">
                  {formData.llm_rotate_designer
                    ? `Minimum ${minParticipants} participants required for designer rotation`
                    : "Add LLM models to participate in the test set"}
                </p>
              </div>
              <Button onClick={addParticipant} size="sm">
                <Plus className="h-4 w-4 mr-2" />
                Add Participant
              </Button>
            </div>

            {modelsError && (
              <Alert>
                <Info className="h-4 w-4" />
                <AlertDescription>{modelsError}</AlertDescription>
              </Alert>
            )}

            {formData.llm_rotate_designer && formData.participants.length < 3 && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  Designer rotation requires at least 3 participants. Please add more participants.
                </AlertDescription>
              </Alert>
            )}

            <div className="space-y-4">
              {formData.participants.map((participant, index) => (
                <Card key={index}>
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-base">Participant {index + 1}</CardTitle>
                      {formData.participants.length > minParticipants && (
                        <Button variant="ghost" size="sm" onClick={() => removeParticipant(index)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="space-y-2">
                      <Label>Model</Label>
                      {modelsLoading ? (
                        <div className="flex items-center space-x-2">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span className="text-sm text-muted-foreground">Loading models...</span>
                        </div>
                      ) : (
                        <Select
                          value={participant.model_name}
                          onValueChange={(value) => updateParticipant(index, "model_name", value)}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {availableModels.map((model) => (
                              <SelectItem key={model.id} value={model.id}>
                                {model.id}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </div>

                    <Collapsible>
                      <CollapsibleTrigger asChild>
                        <Button variant="ghost" size="sm" className="w-full justify-between">
                          Advanced Parameters
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                      </CollapsibleTrigger>
                      <CollapsibleContent className="space-y-4 pt-4">
                        <div className="grid grid-cols-2 gap-4">
                          <div className="space-y-2">
                            <Label>Temperature</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="0"
                              max="2"
                              value={participant.model_params?.temperature ?? ""}
                              onChange={(e) =>
                                updateParticipantParam(
                                  index,
                                  "temperature",
                                  e.target.value ? Number.parseFloat(e.target.value) : undefined,
                                )
                              }
                              placeholder="0.7"
                            />
                          </div>
                          <div className="space-y-2">
                            <Label>Max Tokens</Label>
                            <Input
                              type="number"
                              min="1"
                              max="4096"
                              value={participant.model_params?.maxCompletionTokens ?? ""}
                              onChange={(e) =>
                                updateParticipantParam(
                                  index,
                                  "maxCompletionTokens",
                                  e.target.value ? Number.parseInt(e.target.value) : undefined,
                                )
                              }
                              placeholder="2000"
                            />
                          </div>
                          <div className="space-y-2">
                            <Label>Top P</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="0"
                              max="1"
                              value={participant.model_params?.topP ?? ""}
                              onChange={(e) =>
                                updateParticipantParam(
                                  index,
                                  "topP",
                                  e.target.value ? Number.parseFloat(e.target.value) : undefined,
                                )
                              }
                              placeholder="1.0"
                            />
                          </div>
                          <div className="space-y-2">
                            <Label>Frequency Penalty</Label>
                            <Input
                              type="number"
                              step="0.1"
                              min="-2"
                              max="2"
                              value={participant.model_params?.frequencyPenalty ?? ""}
                              onChange={(e) =>
                                updateParticipantParam(
                                  index,
                                  "frequencyPenalty",
                                  e.target.value ? Number.parseFloat(e.target.value) : undefined,
                                )
                              }
                              placeholder="0.0"
                            />
                          </div>
                        </div>
                      </CollapsibleContent>
                    </Collapsible>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )

      case "games":
        return (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-medium">Game Configurations</h3>
              <Button onClick={addGame} size="sm">
                <Plus className="h-4 w-4 mr-2" />
                Add Game
              </Button>
            </div>

            <div className="space-y-4">
              {formData.games.map((game, index) => (
                <Card key={index}>
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-base">Game Configuration {index + 1}</CardTitle>
                      {formData.games.length > 1 && (
                        <Button variant="ghost" size="sm" onClick={() => removeGame(index)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-3 gap-4">
                      <div className="space-y-2">
                        <Label>Grid Size</Label>
                        <Select
                          value={game.grid_size.toString()}
                          onValueChange={(value) => updateGame(index, "grid_size", Number.parseInt(value))}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {[3, 4, 5, 6].map((size) => (
                              <SelectItem key={size} value={size.toString()}>
                                {size}×{size}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-2">
                        <Label>Symbols</Label>
                        <Select
                          value={game.num_symbols.toString()}
                          onValueChange={(value) => updateGame(index, "num_symbols", Number.parseInt(value))}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {[2, 3, 4, 5, 6].map((num) => (
                              <SelectItem key={num} value={num.toString()}>
                                {num} symbols
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-2">
                        <Label>Repeat Count</Label>
                        <Input
                          type="number"
                          min="1"
                          max="10"
                          value={game.repeat_count}
                          onChange={(e) => updateGame(index, "repeat_count", Number.parseInt(e.target.value) || 1)}
                        />
                      </div>
                    </div>

                    {!formData.llm_rotate_designer && (
                      <>
                        <div className="space-y-2">
                          <Label>Pattern Generation Mode</Label>
                          <Select
                            value={game.pattern_mode || "Random"}
                            onValueChange={(value) => updateGame(index, "pattern_mode", value)}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="Random">Random</SelectItem>
                              <SelectItem value="Visual">Visual (Symmetry)</SelectItem>
                              <SelectItem value="Algorithmic">Algorithmic (Shift)</SelectItem>
                              <SelectItem value="Custom">Custom (Define Manually)</SelectItem>
                              <SelectItem value="LLM">LLM (AI Generated)</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        {game.pattern_mode === "Visual" && (
                          <div className="space-y-2 pl-4 border-l-2">
                            <Label>Symmetry Type</Label>
                            <Select
                              value={game.symmetry_type || "Left-Right"}
                              onValueChange={(value) => updateGame(index, "symmetry_type", value)}
                            >
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="Left-Right">Left-Right</SelectItem>
                                <SelectItem value="Top-Bottom">Top-Bottom</SelectItem>
                                <SelectItem value="Both">Both</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                        )}

                        {game.pattern_mode === "Algorithmic" && (
                          <div className="space-y-2 pl-4 border-l-2">
                            <Label>Shift Step (1-{Math.max(1, game.num_symbols - 1)})</Label>
                            <Input
                              type="number"
                              min="1"
                              max={Math.max(1, game.num_symbols - 1)}
                              value={game.shift_step || 1}
                              onChange={(e) => updateGame(index, "shift_step", Number.parseInt(e.target.value))}
                            />
                          </div>
                        )}

                        {game.pattern_mode === "Custom" && (
                          <div className="space-y-2 pl-4 border-l-2">
                            <div className="flex items-center justify-between">
                              <Label>Custom Pattern</Label>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                  setEditingGameIndex(index)
                                  setCurrentStep("customPatternEditor")
                                }}
                              >
                                <Edit className="h-4 w-4 mr-2" />
                                {game.custom_pattern ? "Edit Pattern" : "Define Pattern"}
                              </Button>
                            </div>
                            {game.custom_pattern && (
                              <div className="mt-2">
                                <Label className="text-sm text-muted-foreground">Pattern Preview:</Label>
                                <div className="mt-1">
                                  <GameBoard
                                    grid={game.custom_pattern}
                                    gridSize={game.grid_size}
                                    symbolsInUse={allSymbols.slice(0, game.num_symbols)}
                                    onCellClick={() => {}}
                                    selectedCells={[]}
                                    queriedCells={[]}
                                    isGuessing={false}
                                    finalGuess={null}
                                    masterPattern={game.custom_pattern}
                                    isGameOver={true}
                                    readOnly={true}
                                  />
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                        {game.pattern_mode === "LLM" && (
                          <div className="space-y-4 pl-4 border-l-2">
                            <div className="space-y-2">
                              <Label>LLM Model</Label>
                              {modelsLoading ? (
                                <div className="flex items-center space-x-2">
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                  <span className="text-sm text-muted-foreground">Loading models...</span>
                                </div>
                              ) : (
                                <Select
                                  value={game.llm_pattern_model || "chatgpt-4o-latest"}
                                  onValueChange={(value) => updateGame(index, "llm_pattern_model", value)}
                                >
                                  <SelectTrigger>
                                    <SelectValue />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {availableModels.map((model) => (
                                      <SelectItem key={model.id} value={model.id}>
                                        {model.id}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              )}
                            </div>

                            <Collapsible>
                              <CollapsibleTrigger asChild>
                                <Button variant="ghost" size="sm" className="w-full justify-between">
                                  Advanced Model Parameters
                                  <ChevronDown className="h-4 w-4" />
                                </Button>
                              </CollapsibleTrigger>
                              <CollapsibleContent className="space-y-4 pt-4">
                                <div className="grid grid-cols-2 gap-4">
                                  <div className="space-y-2">
                                    <Label>Temperature</Label>
                                    <Input
                                      type="number"
                                      step="0.1"
                                      min="0"
                                      max="2"
                                      value={game.llm_pattern_model_params?.temperature ?? ""}
                                      onChange={(e) =>
                                        updateGameLLMParam(
                                          index,
                                          "temperature",
                                          e.target.value ? Number.parseFloat(e.target.value) : undefined,
                                        )
                                      }
                                      placeholder="0.7"
                                    />
                                  </div>
                                  <div className="space-y-2">
                                    <Label>Max Tokens</Label>
                                    <Input
                                      type="number"
                                      min="1"
                                      max="4096"
                                      value={game.llm_pattern_model_params?.maxCompletionTokens ?? ""}
                                      onChange={(e) =>
                                        updateGameLLMParam(
                                          index,
                                          "maxCompletionTokens",
                                          e.target.value ? Number.parseInt(e.target.value) : undefined,
                                        )
                                      }
                                      placeholder="2000"
                                    />
                                  </div>
                                  <div className="space-y-2">
                                    <Label>Top P</Label>
                                    <Input
                                      type="number"
                                      step="0.1"
                                      min="0"
                                      max="1"
                                      value={game.llm_pattern_model_params?.topP ?? ""}
                                      onChange={(e) =>
                                        updateGameLLMParam(
                                          index,
                                          "topP",
                                          e.target.value ? Number.parseFloat(e.target.value) : undefined,
                                        )
                                      }
                                      placeholder="1.0"
                                    />
                                  </div>
                                  <div className="space-y-2">
                                    <Label>Frequency Penalty</Label>
                                    <Input
                                      type="number"
                                      step="0.1"
                                      min="-2"
                                      max="2"
                                      value={game.llm_pattern_model_params?.frequencyPenalty ?? ""}
                                      onChange={(e) =>
                                        updateGameLLMParam(
                                          index,
                                          "frequencyPenalty",
                                          e.target.value ? Number.parseFloat(e.target.value) : undefined,
                                        )
                                      }
                                      placeholder="0.0"
                                    />
                                  </div>
                                </div>
                              </CollapsibleContent>
                            </Collapsible>

                            <div className="space-y-2">
                              <Label>Custom Prompt (Optional)</Label>
                              <Textarea
                                value={game.llm_pattern_prompt || ""}
                                onChange={(e) => updateGame(index, "llm_pattern_prompt", e.target.value || undefined)}
                                placeholder="e.g., 'Create a complex symmetrical pattern' or 'Design a spiral pattern'"
                                rows={2}
                              />
                            </div>

                            <Button
                              onClick={() => handleRequestLLMPattern(index)}
                              disabled={isLLMDesigning[index] || !game.llm_pattern_model}
                              className="w-full"
                            >
                              {isLLMDesigning[index] ? (
                                <>
                                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                  Generating Pattern...
                                </>
                              ) : (
                                "Generate Pattern with LLM"
                              )}
                            </Button>

                            {llmDesignErrors[index] && (
                              <Alert variant="destructive">
                                <AlertTriangle className="h-4 w-4" />
                                <AlertDescription>{llmDesignErrors[index]}</AlertDescription>
                              </Alert>
                            )}

                            {game.llm_designed_pattern && !llmDesignErrors[index] && (
                              <div className="space-y-2">
                                <Label className="text-sm text-muted-foreground">Generated Pattern Preview:</Label>
                                <div className="mt-1">
                                  <GameBoard
                                    grid={game.llm_designed_pattern}
                                    gridSize={game.grid_size}
                                    symbolsInUse={allSymbols.slice(0, game.num_symbols)}
                                    onCellClick={() => {}}
                                    selectedCells={[]}
                                    queriedCells={[]}
                                    isGuessing={false}
                                    finalGuess={null}
                                    masterPattern={game.llm_designed_pattern}
                                    isGameOver={true}
                                    readOnly={true}
                                  />
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </>
                    )}

                    {formData.llm_rotate_designer && (
                      <div className="space-y-2">
                        <Label>Optional Prompt (for LLM designers)</Label>
                        <Textarea
                          value={game.optional_prompt || ""}
                          onChange={(e) => updateGame(index, "optional_prompt", e.target.value || undefined)}
                          placeholder="Additional instructions for pattern generation..."
                          rows={2}
                        />
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>

            <Alert>
              <CheckCircle className="h-4 w-4" />
              <AlertDescription>
                Total games to be executed: <strong>{calculateTotalGames()}</strong>
                {formData.llm_rotate_designer && (
                  <span className="block text-sm mt-1">
                    ({formData.participants.length} participants ×{" "}
                    {formData.games.reduce((sum, g) => sum + g.repeat_count, 0)} game configs)
                  </span>
                )}
              </AlertDescription>
            </Alert>
          </div>
        )

      default:
        return null
    }
  }

  const isCustomPatternEditor = currentStep === "customPatternEditor"
  const displayStepIndex = isCustomPatternEditor ? stepOrder.length : getCurrentStepIndex() + 1
  const displayStepTitle = isCustomPatternEditor ? "Custom Pattern Editor" : stepTitles[getCurrentStepIndex()]

  return (
    <div className="container mx-auto p-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/test-sets">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to Test Sets
          </Link>
        </Button>
        <Separator orientation="vertical" className="h-6" />
        <div>
          <h1 className="text-2xl font-bold">Create Test Set</h1>
          <p className="text-muted-foreground">Set up a new competitive evaluation for LLMs</p>
        </div>
      </div>

      {!isCustomPatternEditor && (
        <>
          {/* Progress */}
          <div className="mb-8">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">
                Step {displayStepIndex} of {stepOrder.length}
              </span>
              <span className="text-sm text-muted-foreground">{displayStepTitle}</span>
            </div>
            <Progress value={(displayStepIndex / stepOrder.length) * 100} className="w-full" />
          </div>

          {/* Step Navigation */}
          <div className="flex items-center justify-center mb-8">
            <div className="flex items-center space-x-4">
              {stepTitles.map((title, index) => {
                const stepNumber = index + 1
                const isActive = stepNumber === displayStepIndex
                const isCompleted = stepNumber < displayStepIndex

                return (
                  <div key={stepNumber} className="flex items-center">
                    <div
                      className={`
                      flex items-center justify-center w-8 h-8 rounded-full text-sm font-medium
                      ${
                        isActive
                          ? "bg-primary text-primary-foreground"
                          : isCompleted
                            ? "bg-green-500 text-white"
                            : "bg-muted text-muted-foreground"
                      }
                    `}
                    >
                      {isCompleted ? <CheckCircle className="h-4 w-4" /> : stepNumber}
                    </div>
                    <span className={`ml-2 text-sm ${isActive ? "font-medium" : "text-muted-foreground"}`}>
                      {title}
                    </span>
                    {stepNumber < stepTitles.length && <ChevronRight className="h-4 w-4 mx-4 text-muted-foreground" />}
                  </div>
                )
              })}
            </div>
          </div>
        </>
      )}

      {/* Error Alert */}
      {error && (
        <Alert variant="destructive" className="mb-6">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Step Content */}
      {isCustomPatternEditor ? (
        renderStepContent()
      ) : (
        <Card className="mb-8">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {currentStep === "basic" && <Info className="h-5 w-5" />}
              {currentStep === "settings" && <Settings className="h-5 w-5" />}
              {currentStep === "participants" && <Users className="h-5 w-5" />}
              {currentStep === "games" && <GamepadIcon className="h-5 w-5" />}
              {displayStepTitle}
            </CardTitle>
            <CardDescription>
              {currentStep === "basic" && "Provide basic information about your test set"}
              {currentStep === "settings" && "Choose how the test set should be executed"}
              {currentStep === "participants" && "Configure the LLM models that will participate"}
              {currentStep === "games" && "Define the game configurations to test"}
            </CardDescription>
          </CardHeader>
          <CardContent>{renderStepContent()}</CardContent>
        </Card>
      )}

      {!isCustomPatternEditor && (
        <div className="flex items-center justify-between">
          <Button variant="outline" onClick={handlePrevious} disabled={currentStep === "basic"}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Previous
          </Button>

          <div className="flex items-center gap-2">
            {currentStep !== "games" ? (
              <Button onClick={handleNext} disabled={!validateCurrentStep()}>
                Next
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            ) : (
              <Button onClick={handleSubmit} disabled={!validateCurrentStep() || loading}>
                {loading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Creating...
                  </>
                ) : (
                  "Create Test Set"
                )}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
