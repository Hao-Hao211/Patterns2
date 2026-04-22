"use client"

import { useState, useEffect, useRef, useCallback, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
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
import { GameHistorySelector } from "@/components/game-history-selector"
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
  Copy,
  ExternalLink,
  Play,
} from "lucide-react"

interface LLMModelParams {
  temperature?: number
  maxCompletionTokens?: number
  topP?: number
  frequencyPenalty?: number
  presencePenalty?: number
  reasoningEffort?: string
  chatHistoryEnabled?: boolean
}

interface EvolvingConfig {
  enabled: boolean
  mode: 'fresh' | 'imported'
  import_game_ids?: string[]
  accumulate: boolean
}

interface TestSetParticipant {
  participant_type: 'Human' | 'LLM'
  human_name?: string
  model_name?: string
  model_params?: LLMModelParams
  evolving_config?: EvolvingConfig
}

interface TestSetGameConfig {
  grid_size: number
  num_symbols: number
  optional_prompt?: string
  custom_pattern?: Grid
  repeat_count: number
  pattern_mode?: "Visual" | "Algorithmic" | "Custom" | "Random" | "LLM" | "LLM_Designer"
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
  supported_parameters?: string[]
}

interface ModelsListResponse {
  object: string
  data: OpenAIModel[]
}

type SetupStep = "basic" | "settings" | "participants" | "games" | "customPatternEditor" | "humanTest"

const allSymbols: Symbol[] = ["+", "○", "△", "□", "★", "✖"]

function CreateTestSetPageInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const cloneId = searchParams.get('clone')
  const [currentStep, setCurrentStep] = useState<SetupStep>("basic")
  const [editingGameIndex, setEditingGameIndex] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [availableModels, setAvailableModels] = useState<OpenAIModel[]>([])
  const [modelsLoading, setModelsLoading] = useState(true)
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [existingHumanNames, setExistingHumanNames] = useState<string[]>([])

  const [isLLMDesigning, setIsLLMDesigning] = useState<Record<number, boolean>>({})
  const [llmDesignErrors, setLlmDesignErrors] = useState<Record<number, string | null>>({})

  // Human test step state
  const [joinToken, setJoinToken] = useState<string | null>(null)
  const [joinUrl, setJoinUrl] = useState<string | null>(null)
  const [testSetId, setTestSetId] = useState<string | null>(null)
  const [playerSlots, setPlayerSlots] = useState<any[]>([])
  const [humanTestCreated, setHumanTestCreated] = useState(false)
  const [humanTestCreating, setHumanTestCreating] = useState(false)
  const [humanTestStarting, setHumanTestStarting] = useState(false)
  const [copied, setCopied] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Form data
  const [formData, setFormData] = useState<TestSetCreateRequest>({
    name: "",
    description: "",
    participants: [{ participant_type: 'LLM' as const, model_name: "chatgpt-4o-latest" }],
    llm_rotate_designer: true,
    games: [
      {
        grid_size: 6,
        num_symbols: 5,
        repeat_count: 1,
      },
    ],
  })

  const [enableHumanTest, setEnableHumanTest] = useState(false)
  const isAllHuman = formData.participants.length > 0 &&
    formData.participants.every(p => p.participant_type === 'Human')
  const stepOrder: SetupStep[] = isAllHuman
    ? ["basic", "settings", "participants", "games", "humanTest"]
    : ["basic", "settings", "participants", "games"]
  const stepTitles = isAllHuman
    ? ["Basic Info", "Settings", "Participants", "Games", "Human Test"]
    : ["Basic Info", "Settings", "Participants", "Games"]

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
            participants: prev.participants.map((p) =>
              p.participant_type === 'LLM' ? { ...p, model_name: defaultModel.id } : p
            ),
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

    // Fetch existing human player names for uniqueness validation
    const fetchHumanNames = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/human-names`)
        if (res.ok) {
          const data = await res.json()
          setExistingHumanNames(data.names || [])
        }
      } catch (err) {
        console.error("Failed to fetch human names:", err)
      }
    }
    fetchHumanNames()
  }, [])

  // Clone: fetch source test set config and pre-populate form
  useEffect(() => {
    if (!cloneId || modelsLoading) return

    const fetchCloneSource = async () => {
      try {
        const response = await fetch(
          `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${cloneId}`
        )
        if (!response.ok) throw new Error('Failed to fetch test set')

        const data = await response.json()
        const config = data.config

        setFormData({
          name: `${data.name} (Copy)`,
          description: data.description || '',
          participants: (config.participants || []).map((p: any) => ({
            participant_type: p.participant_type || 'LLM',
            model_name: p.model_name,
            model_params: p.model_params || undefined,
            evolving_config: p.evolving_config || undefined,
          })),
          llm_rotate_designer: config.llm_rotate_designer ?? true,
          games: (config.games || []).map((g: any) => ({
            grid_size: g.grid_size ?? 6,
            num_symbols: g.num_symbols ?? 5,
            optional_prompt: g.optional_prompt,
            custom_pattern: g.custom_pattern,
            repeat_count: g.repeat_count ?? 1,
            pattern_mode: g.pattern_mode,
            symmetry_type: g.symmetry_type,
            shift_step: g.shift_step,
            llm_pattern_model: g.llm_pattern_model,
            llm_pattern_model_params: g.llm_pattern_model_params,
            llm_pattern_prompt: g.llm_pattern_prompt,
            llm_designed_pattern: g.llm_designed_pattern,
          })),
        })
      } catch (err) {
        console.error('Failed to clone test set:', err)
        setError('Failed to load test set for cloning')
      }
    }

    fetchCloneSource()
  }, [cloneId, modelsLoading])

  // Auto-disable designer rotation for all-human test sets
  useEffect(() => {
    if (isAllHuman && formData.llm_rotate_designer) {
      setFormData((prev) => ({ ...prev, llm_rotate_designer: false }))
    }
  }, [isAllHuman, formData.llm_rotate_designer])

  // Auto-adjust participants when designer rotation changes
  useEffect(() => {
    if (formData.llm_rotate_designer) {
      const llmCount = formData.participants.filter(p => p.participant_type === 'LLM').length
      if (llmCount < 3) {
        const defaultModel = availableModels.length > 0 ? availableModels[0].id : "chatgpt-4o-latest"
        const newParticipants = [...formData.participants]
        while (newParticipants.filter(p => p.participant_type === 'LLM').length < 3) {
          newParticipants.push({ participant_type: 'LLM', model_name: defaultModel })
        }
        setFormData((prev) => ({
          ...prev,
          participants: newParticipants,
        }))
      }
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

  const addParticipant = (type: 'Human' | 'LLM' = 'LLM') => {
    if (type === 'Human') {
      setFormData((prev) => ({
        ...prev,
        participants: [...prev.participants, { participant_type: 'Human', human_name: '' }],
      }))
    } else {
      const defaultModel = availableModels.length > 0 ? availableModels[0].id : "chatgpt-4o-latest"
      setFormData((prev) => ({
        ...prev,
        participants: [...prev.participants, { participant_type: 'LLM', model_name: defaultModel }],
      }))
    }
  }

  const removeParticipant = (index: number) => {
    if (formData.participants.length > 1) {
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

  const updateParticipantParam = (participantIndex: number, param: keyof LLMModelParams, value: number | string | boolean | undefined) => {
    setFormData((prev) => ({
      ...prev,
      participants: prev.participants.map((p, i) => {
        if (i === participantIndex) {
          const newParams = { ...p.model_params }
          if (value === undefined || value === null) {
            delete newParams[param]
          } else {
            (newParams as Record<string, unknown>)[param] = value
          }
          return { ...p, model_params: Object.keys(newParams).length > 0 ? newParams : undefined }
        }
        return p
      }),
    }))
  }

  const modelSupportsReasoning = (modelName: string): boolean => {
    const model = availableModels.find((m) => m.id === modelName)
    return model?.supported_parameters?.includes("reasoning") ?? false
  }

  const updateParticipantEvolvingConfig = (index: number, config: EvolvingConfig) => {
    setFormData((prev) => ({
      ...prev,
      participants: prev.participants.map((p, i) =>
        i === index ? { ...p, evolving_config: config } : p
      ),
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
            if (g.pattern_mode === "LLM" || g.pattern_mode === "LLM_Designer") {
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
            if (g.pattern_mode === "LLM" || g.pattern_mode === "LLM_Designer") {
              updatedGame.llm_pattern_model = undefined
              updatedGame.llm_pattern_model_params = undefined
              updatedGame.llm_pattern_prompt = undefined
              updatedGame.llm_designed_pattern = undefined
            }
          } else if (value === "Custom") {
            // When switching to Custom mode, keep existing custom_pattern if available
            updatedGame.symmetry_type = undefined
            updatedGame.shift_step = undefined
            if (g.pattern_mode === "LLM" || g.pattern_mode === "LLM_Designer") {
              updatedGame.llm_pattern_model = undefined
              updatedGame.llm_pattern_model_params = undefined
              updatedGame.llm_pattern_prompt = undefined
              updatedGame.llm_designed_pattern = undefined
            }
          } else if (value === "LLM" || value === "LLM_Designer") {
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

  const updateGameLLMParam = (gameIndex: number, param: keyof LLMModelParams, value: number | string | boolean | undefined) => {
    setFormData((prev) => ({
      ...prev,
      games: prev.games.map((g, i) => {
        if (i === gameIndex) {
          const newParams = { ...g.llm_pattern_model_params }
          if (value === undefined || value === null) {
            delete newParams[param]
          } else {
            ;(newParams as any)[param] = value
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

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [])

  const pollJoinStatus = useCallback((tk: string, tsId?: string) => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/join/${tk}`)
        if (res.ok) {
          const data = await res.json()
          setPlayerSlots(data.participant_slots || [])
          // Auto-navigate when backend auto-starts (all players joined)
          if (data.status === 'running' || data.status === 'created') {
            if (pollingRef.current) clearInterval(pollingRef.current)
            const navigateId = tsId || testSetId
            if (navigateId) {
              router.push(`/test-sets/${navigateId}/execute`)
            }
          }
        }
      } catch (err) {
        console.error("Failed to poll join status:", err)
      }
    }, 3000)
  }, [testSetId, router])

  const createHumanTest = async () => {
    setHumanTestCreating(true)
    setError(null)

    try {
      const apiFormData = {
        ...formData,
        enable_human_test: true,
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

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(apiFormData),
      })

      if (response.ok) {
        const result = await response.json()
        const token = result.join_token
        const id = result.test_set_id || result.id
        setTestSetId(id)
        setJoinToken(token)
        setJoinUrl(`${window.location.origin}/join/${token}`)
        setHumanTestCreated(true)
        pollJoinStatus(token, id)
      } else {
        const errorData = await response.json()
        throw new Error(errorData.detail || "Failed to create test set")
      }
    } catch (err) {
      console.error("Error creating human test:", err)
      setError(err instanceof Error ? err.message : "Failed to create human test")
    } finally {
      setHumanTestCreating(false)
    }
  }

  const startHumanTest = async () => {
    if (!testSetId) return
    setHumanTestStarting(true)
    setError(null)

    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/test-sets/${testSetId}/start`, {
        method: "POST",
      })

      if (response.ok) {
        if (pollingRef.current) clearInterval(pollingRef.current)
        router.push(`/test-sets/${testSetId}/execute`)
      } else {
        const errorData = await response.json()
        throw new Error(errorData.detail || "Failed to start test")
      }
    } catch (err) {
      console.error("Error starting human test:", err)
      setError(err instanceof Error ? err.message : "Failed to start human test")
    } finally {
      setHumanTestStarting(false)
    }
  }

  const handleCopyJoinUrl = async () => {
    if (!joinUrl) return
    try {
      await navigator.clipboard.writeText(joinUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error("Failed to copy:", err)
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
    const totalGameConfigs = formData.games.reduce((sum, game) => sum + game.repeat_count, 0)
    const participantCount = formData.participants.length
    if (participantCount === 0 || totalGameConfigs === 0) return 0

    const humanCount = formData.participants.filter(p => p.participant_type === 'Human').length
    const hasLLMs = formData.participants.some(p => p.participant_type !== 'Human')

    if (formData.llm_rotate_designer) {
      // Each LLM takes turns as designer
      const llmCount = formData.participants.filter(p => p.participant_type === 'LLM').length
      return totalGameConfigs * llmCount
    }

    if (humanCount === 0) {
      // All LLM, no rotation: all LLMs play together in each game
      return totalGameConfigs
    }

    // Has humans: each human plays separately, LLMs play together in one group
    const llmGames = hasLLMs ? totalGameConfigs : 0
    const humanGames = totalGameConfigs * humanCount
    return llmGames + humanGames
  }

  const validateCurrentStep = () => {
    switch (currentStep) {
      case "basic":
        return formData.name.trim().length > 0
      case "settings":
        return true // Settings step is always valid
      case "participants":
        const llmParticipants = formData.participants.filter(p => p.participant_type === 'LLM')
        const minLLMParticipants = formData.llm_rotate_designer ? 3 : 0
        return (
          formData.participants.length >= 1 &&
          llmParticipants.length >= minLLMParticipants &&
          formData.participants.every((p) =>
            p.participant_type === 'Human'
              ? (p.human_name?.trim() || '').length > 0
              : !!p.model_name
          ) &&
          // Check human name uniqueness within form and against DB
          (() => {
            const humanParticipants = formData.participants.filter(p => p.participant_type === 'Human')
            const humanNames = humanParticipants.map(p => (p.human_name?.trim() || '').toLowerCase())
            // No duplicates within form
            if (new Set(humanNames).size !== humanNames.length) return false
            // No conflicts with existing DB names
            if (humanNames.some(n => existingHumanNames.some(e => e.toLowerCase() === n))) return false
            return true
          })()
        )
      case "games":
        return (
          formData.games.length > 0 &&
          formData.games.every(
            (g) =>
              g.grid_size >= 3 &&
              g.grid_size <= 8 &&
              g.num_symbols >= 2 &&
              g.num_symbols <= 6 &&
              g.repeat_count >= 1 &&
              (formData.llm_rotate_designer || g.pattern_mode) &&
              (g.pattern_mode !== "Custom" || g.custom_pattern) &&
              (g.pattern_mode !== "LLM" || g.llm_designed_pattern) &&
              (g.pattern_mode !== "LLM_Designer" || g.llm_pattern_model),
          )
        )
      case "humanTest":
        return true // Human test step validation is handled by the step's own UI
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
                  checked={isAllHuman ? false : formData.llm_rotate_designer}
                  disabled={isAllHuman}
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

              {isAllHuman && (
                <Alert>
                  <Info className="h-4 w-4" />
                  <AlertDescription>
                    Designer rotation is automatically disabled for human-only test sets.
                  </AlertDescription>
                </Alert>
              )}

              {formData.llm_rotate_designer && !isAllHuman && (
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
        const llmParticipantCount = formData.participants.filter(p => p.participant_type === 'LLM').length
        return (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-medium">Participants</h3>
                <p className="text-sm text-muted-foreground">
                  {formData.llm_rotate_designer
                    ? `Minimum 3 LLM participants required for designer rotation. Human players are scientists only.`
                    : "Add LLM models or human players to participate in the test set"}
                </p>
              </div>
              <div className="flex gap-2">
                <Button onClick={() => addParticipant('LLM')} size="sm">
                  <Plus className="h-4 w-4 mr-2" />
                  Add LLM
                </Button>
                <Button onClick={() => addParticipant('Human')} size="sm" variant="outline">
                  <Plus className="h-4 w-4 mr-2" />
                  Add Human
                </Button>
              </div>
            </div>

            {modelsError && (
              <Alert>
                <Info className="h-4 w-4" />
                <AlertDescription>{modelsError}</AlertDescription>
              </Alert>
            )}

            {formData.llm_rotate_designer && llmParticipantCount < 3 && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  Designer rotation requires at least 3 LLM participants. Currently have {llmParticipantCount}. Human players cannot be designers.
                </AlertDescription>
              </Alert>
            )}

            <div className="space-y-4">
              {formData.participants.map((participant, index) => (
                <Card key={index}>
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <CardTitle className="text-base">Participant {index + 1}</CardTitle>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          participant.participant_type === 'Human'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-purple-100 text-purple-700'
                        }`}>
                          {participant.participant_type === 'Human' ? 'Human' : 'LLM'}
                        </span>
                      </div>
                      {formData.participants.length > 1 && (
                        <Button variant="ghost" size="sm" onClick={() => removeParticipant(index)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {participant.participant_type === 'Human' ? (
                      <div className="space-y-3">
                          <>
                            <div className="space-y-2">
                              <Label>Player Name <span className="text-red-500">*</span></Label>
                              <Input
                                placeholder="Enter a unique nickname"
                                value={participant.human_name || ''}
                                onChange={(e) => updateParticipant(index, "human_name" as any, e.target.value)}
                              />
                              {(() => {
                                const name = (participant.human_name?.trim() || '').toLowerCase()
                                if (!name) return null
                                const duplicateInForm = formData.participants.some(
                                  (p, i) => i !== index && p.participant_type === 'Human' &&
                                    (p.human_name?.trim() || '').toLowerCase() === name
                                )
                                if (duplicateInForm) {
                                  return <p className="text-xs text-red-500">This name is already used by another participant in this form.</p>
                                }
                                if (existingHumanNames.some(n => n.toLowerCase() === name)) {
                                  return <p className="text-xs text-red-500">This name already exists in the database. Please choose a different name.</p>
                                }
                                return null
                              })()}
                            </div>
                            <p className="text-sm text-muted-foreground">
                              Human player — will interact through the execution page during games.
                            </p>
                          </>
                        {formData.llm_rotate_designer && (
                          <p className="text-xs text-amber-600">
                            Note: Human players can only be scientists, not designers.
                          </p>
                        )}
                      </div>
                    ) : (
                    <>
                    <div className="space-y-2">
                      <Label>Model</Label>
                      {modelsLoading ? (
                        <div className="flex items-center space-x-2">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span className="text-sm text-muted-foreground">Loading models...</span>
                        </div>
                      ) : (
                        <Select
                          value={participant.model_name || ''}
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
                              max="65536"
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
                        {participant.model_name && modelSupportsReasoning(participant.model_name) && (
                          <div className="space-y-2 pt-2">
                            <Label>Reasoning Effort</Label>
                            <Select
                              value={participant.model_params?.reasoningEffort ?? "__not_set__"}
                              onValueChange={(value) =>
                                updateParticipantParam(
                                  index,
                                  "reasoningEffort",
                                  value === "__not_set__" ? undefined : value,
                                )
                              }
                            >
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="__not_set__">Not set (default)</SelectItem>
                                <SelectItem value="none">None (disabled)</SelectItem>
                                <SelectItem value="minimal">Minimal</SelectItem>
                                <SelectItem value="low">Low</SelectItem>
                                <SelectItem value="medium">Medium</SelectItem>
                                <SelectItem value="high">High</SelectItem>
                                <SelectItem value="xhigh">XHigh (maximum)</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                        )}
                        <div className="flex items-center justify-between pt-2">
                          <div className="space-y-0.5">
                            <Label>Chat History (Multi-turn)</Label>
                            <p className="text-xs text-muted-foreground">
                              Off = single-turn (each turn independent). On = accumulate conversation history.
                            </p>
                          </div>
                          <Switch
                            checked={participant.model_params?.chatHistoryEnabled ?? false}
                            onCheckedChange={(checked) =>
                              updateParticipantParam(index, "chatHistoryEnabled", checked || undefined)
                            }
                          />
                        </div>
                      </CollapsibleContent>
                    </Collapsible>

                    <Collapsible>
                      <CollapsibleTrigger asChild>
                        <Button variant="ghost" size="sm" className="w-full justify-between">
                          Evolving Configuration
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                      </CollapsibleTrigger>
                      <CollapsibleContent className="space-y-4 pt-4">
                        <div className="flex items-center justify-between">
                          <div className="space-y-1">
                            <Label>Enable Evolving</Label>
                            <p className="text-xs text-muted-foreground">
                              Accumulate game experience across rounds to test learning ability
                            </p>
                          </div>
                          <Switch
                            checked={participant.evolving_config?.enabled || false}
                            onCheckedChange={(checked) =>
                              updateParticipantEvolvingConfig(index, {
                                ...(participant.evolving_config || { mode: 'fresh', accumulate: true }),
                                enabled: checked,
                              })
                            }
                          />
                        </div>

                        {participant.evolving_config?.enabled && (
                          <>
                            <div className="space-y-2">
                              <Label>Mode</Label>
                              <Select
                                value={participant.evolving_config.mode || 'fresh'}
                                onValueChange={(value) =>
                                  updateParticipantEvolvingConfig(index, {
                                    ...participant.evolving_config!,
                                    mode: value as 'fresh' | 'imported',
                                  })
                                }
                              >
                                <SelectTrigger>
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="fresh">Fresh Start (empty history, accumulates)</SelectItem>
                                  <SelectItem value="imported">Import from Existing Games</SelectItem>
                                </SelectContent>
                              </Select>
                              <p className="text-xs text-muted-foreground">
                                {participant.evolving_config.mode === 'fresh'
                                  ? "Starts with no history. After each game, the result is added as context for the next game."
                                  : "Import history from previous games as initial context."}
                              </p>
                            </div>

                            {participant.evolving_config.mode === 'imported' && (
                              <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                  <div className="space-y-1">
                                    <Label>Continue Accumulating</Label>
                                    <p className="text-xs text-muted-foreground">
                                      {participant.evolving_config.accumulate !== false
                                        ? "Imported history + new game results will be added progressively"
                                        : "Only the imported history will be used (fixed, never updated)"}
                                    </p>
                                  </div>
                                  <Switch
                                    checked={participant.evolving_config.accumulate !== false}
                                    onCheckedChange={(checked) =>
                                      updateParticipantEvolvingConfig(index, {
                                        ...participant.evolving_config!,
                                        accumulate: checked,
                                      })
                                    }
                                  />
                                </div>

                                <GameHistorySelector
                                  modelName={participant.model_name || ''}
                                  selectedGameIds={participant.evolving_config.import_game_ids || []}
                                  onSelectionChange={(ids) =>
                                    updateParticipantEvolvingConfig(index, {
                                      ...participant.evolving_config!,
                                      import_game_ids: ids,
                                    })
                                  }
                                />
                              </div>
                            )}
                          </>
                        )}
                      </CollapsibleContent>
                    </Collapsible>
                    </>
                    )}
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
                            {[3, 4, 5, 6, 7, 8].map((size) => (
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
                              <SelectItem value="LLM_Designer">LLM Designer (Per-Game)</SelectItem>
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

                        {(game.pattern_mode === "LLM" || game.pattern_mode === "LLM_Designer") && (
                          <div className="space-y-4 pl-4 border-l-2">
                            {game.pattern_mode === "LLM_Designer" && (
                              <p className="text-sm text-muted-foreground">
                                A fresh pattern will be designed by the selected LLM for each game at runtime.
                              </p>
                            )}
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
                                      max="65536"
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
                                {modelSupportsReasoning(game.llm_pattern_model || "") && (
                                  <div className="space-y-2 col-span-2 pt-2">
                                    <Label>Reasoning Effort</Label>
                                    <Select
                                      value={game.llm_pattern_model_params?.reasoningEffort ?? "__not_set__"}
                                      onValueChange={(value) =>
                                        updateGameLLMParam(
                                          index,
                                          "reasoningEffort",
                                          value === "__not_set__" ? undefined : value,
                                        )
                                      }
                                    >
                                      <SelectTrigger>
                                        <SelectValue />
                                      </SelectTrigger>
                                      <SelectContent>
                                        <SelectItem value="__not_set__">Not set (default)</SelectItem>
                                        <SelectItem value="none">None (disabled)</SelectItem>
                                        <SelectItem value="minimal">Minimal</SelectItem>
                                        <SelectItem value="low">Low</SelectItem>
                                        <SelectItem value="medium">Medium</SelectItem>
                                        <SelectItem value="high">High</SelectItem>
                                        <SelectItem value="xhigh">XHigh (maximum)</SelectItem>
                                      </SelectContent>
                                    </Select>
                                  </div>
                                )}
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

                            {game.pattern_mode === "LLM" && (
                              <>
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
                              </>
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
                {(() => {
                  const humanCount = formData.participants.filter(p => p.participant_type === 'Human').length
                  const llmCount = formData.participants.filter(p => p.participant_type !== 'Human').length
                  const totalConfigs = formData.games.reduce((sum, g) => sum + g.repeat_count, 0)
                  if (formData.llm_rotate_designer) {
                    return <span className="block text-sm mt-1">({llmCount} LLM designer{llmCount !== 1 ? "s" : ""} × {totalConfigs} game config{totalConfigs !== 1 ? "s" : ""})</span>
                  }
                  if (humanCount > 0 && llmCount > 0) {
                    return <span className="block text-sm mt-1">({llmCount} LLM × {totalConfigs} games + {humanCount} human × {totalConfigs} games)</span>
                  }
                  return <span className="block text-sm mt-1">({formData.participants.length} participant{formData.participants.length !== 1 ? "s" : ""} × {totalConfigs} game config{totalConfigs !== 1 ? "s" : ""})</span>
                })()}
              </AlertDescription>
            </Alert>

          </div>
        )

      case "humanTest":
        return renderHumanTestStep()

      default:
        return null
    }
  }

  const renderHumanTestStep = () => {
    const totalSlots = formData.participants.length
    const joinedCount = playerSlots.filter((p: any) => p.claimed).length

    return (
      <div className="space-y-6">
        <h3 className="text-lg font-medium">Human Testing Setup</h3>

        {/* Toggle */}
        <Card className="border-blue-200 bg-blue-50/50">
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Users className="h-5 w-5 text-blue-600" />
                  <Label htmlFor="enable-human-test" className="text-base font-medium">
                    Enable Remote Human Testing
                  </Label>
                </div>
                <p className="text-sm text-slate-500 ml-7">
                  Generate a join link so players can play from their own devices.
                  Players will enter their own names when joining — the names set in Step 3 will be replaced.
                </p>
              </div>
              <Switch
                id="enable-human-test"
                checked={enableHumanTest}
                onCheckedChange={setEnableHumanTest}
                disabled={humanTestCreated}
              />
            </div>
          </CardContent>
        </Card>

        {!enableHumanTest ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Remote testing is disabled. Click &quot;Create Test Set&quot; below to create the test set normally.
              You can run it from the Test Sets page.
            </p>
            <Button
              onClick={handleSubmit}
              disabled={loading}
              className="w-full"
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create Test Set"
              )}
            </Button>
          </div>
        ) : !humanTestCreated ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Generate a join link that human players can use to connect to this test session from their own devices.
              All {totalSlots} players must join before the test begins automatically.
            </p>
            <Button
              onClick={createHumanTest}
              disabled={humanTestCreating}
              className="w-full"
            >
              {humanTestCreating ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Generating Join Link...
                </>
              ) : (
                "Generate Join Link"
              )}
            </Button>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Token Display */}
            <div className="text-center space-y-3">
              <p className="text-sm text-muted-foreground">Join Token</p>
              <div className="text-4xl font-mono font-bold tracking-widest bg-muted p-4 rounded-lg">
                {joinToken}
              </div>
            </div>

            {/* Join URL */}
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">Join URL</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 bg-muted p-3 rounded-lg text-sm font-mono break-all">
                  <a href={joinUrl || "#"} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                    {joinUrl}
                    <ExternalLink className="inline h-3 w-3 ml-1" />
                  </a>
                </div>
                <Button variant="outline" size="sm" onClick={handleCopyJoinUrl}>
                  <Copy className="h-4 w-4 mr-1" />
                  {copied ? "Copied!" : "Copy Link"}
                </Button>
              </div>
            </div>

            {/* Player Status */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">Players</p>
                <p className="text-sm text-muted-foreground">
                  Waiting for {totalSlots - joinedCount}/{totalSlots} players to join...
                </p>
              </div>

              <div className="space-y-2">
                {Array.from({ length: totalSlots }).map((_, idx) => {
                  const slot = playerSlots[idx]
                  const isJoined = slot?.claimed
                  return (
                    <div
                      key={idx}
                      className={`flex items-center justify-between p-3 rounded-lg border ${
                        isJoined ? "bg-green-50 border-green-200" : "bg-muted/50 border-dashed"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${isJoined ? "bg-green-500" : "bg-gray-300"}`} />
                        <span className="text-sm">
                          {isJoined ? (slot.player_name || `Player ${idx + 1}`) : `Player ${idx + 1} (waiting...)`}
                        </span>
                      </div>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        isJoined ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                      }`}>
                        {isJoined ? "Joined" : "Empty"}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {joinedCount < totalSlots ? (
              <p className="text-sm text-center text-muted-foreground">
                Waiting for all {totalSlots} players to join. The test will start automatically once everyone has joined.
              </p>
            ) : (
              <div className="text-center space-y-2">
                <Loader2 className="h-6 w-6 animate-spin mx-auto text-blue-500" />
                <p className="text-sm text-blue-600 font-medium">
                  All players joined! Starting test...
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    )
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
          <h1 className="text-2xl font-bold">{cloneId ? "Clone Test Set" : "Create Test Set"}</h1>
          <p className="text-muted-foreground">{cloneId ? "Edit the cloned configuration and create a new test set" : "Set up a new competitive evaluation for LLMs"}</p>
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
              {currentStep === "humanTest" && <Users className="h-5 w-5" />}
              {displayStepTitle}
            </CardTitle>
            <CardDescription>
              {currentStep === "basic" && "Provide basic information about your test set"}
              {currentStep === "settings" && "Choose how the test set should be executed"}
              {currentStep === "participants" && "Configure the LLM models that will participate"}
              {currentStep === "games" && "Define the game configurations to test"}
              {currentStep === "humanTest" && "Share the join link and wait for all players to connect"}
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
            {currentStep === "humanTest" ? (
              // Human test step has its own buttons, no Next/Submit needed
              null
            ) : currentStep === "games" && !isAllHuman ? (
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
            ) : (
              <Button onClick={handleNext} disabled={!validateCurrentStep()}>
                Next
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function CreateTestSetPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-slate-100 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-slate-600"></div>
      </div>
    }>
      <CreateTestSetPageInner />
    </Suspense>
  )
}
