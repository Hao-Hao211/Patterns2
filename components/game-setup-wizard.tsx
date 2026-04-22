"use client"

import { CardFooter } from "@/components/ui/card"
import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { PlusCircle, Trash2, User, Bot, AlertTriangle, Loader2, Settings } from "lucide-react"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import type { Grid, Symbol, LLMModelParams } from "@/types/game-types"
import { GameBoard } from "@/components/game-board"
import { CustomPatternEditor } from "@/components/custom-pattern-editor"
import { Alert, AlertDescription } from "@/components/ui/alert"

// Types
export interface BaseSettingsConfig {
  gridSize: number
  numSymbols: number
}

export interface DesignerConfig {
  type: "Human" | "LLM"
  patternMode?: "Visual" | "Algorithmic" | "Custom" | "Random"
  symmetryType?: "Left-Right" | "Top-Bottom" | "Both"
  shiftStep?: number
  customPattern?: Grid
  llmModel?: string
  llmModelParams?: LLMModelParams
  llmPrompt?: string
  llmDesignedPattern?: Grid
}

export interface PlayerConfig {
  id: string
  name: string
  type: "Human" | "LLM"
  llmModel?: string
  llmModelParams?: LLMModelParams
  finalScore?: number
}

export interface FullGameConfig {
  baseSettings: BaseSettingsConfig
  designer: DesignerConfig
  players: PlayerConfig[]
}

interface GameSetupWizardProps {
  onSetupComplete: (config: FullGameConfig) => void
  allSymbols: Symbol[]
}

interface OpenAIModel {
  id: string
  object: string
  created: number
  owned_by: string
}

interface ModelsResponse {
  object: string
  data: OpenAIModel[]
}

type SetupStep = "basic" | "designer" | "players" | "customPatternEditor"

export function GameSetupWizard({ onSetupComplete, allSymbols }: GameSetupWizardProps) {
  const [step, setStep] = useState<SetupStep>("basic")
  const [baseSettings, setBaseSettings] = useState<BaseSettingsConfig>({ gridSize: 6, numSymbols: 5 })
  const [designerConfig, setDesignerConfig] = useState<DesignerConfig>({ type: "Human", patternMode: "Random" })
  const [players, setPlayers] = useState<PlayerConfig[]>([{ id: "player1", name: "Player 1", type: "Human" }])
  const [isLLMDesigning, setIsLLMDesigning] = useState(false)
  const [llmDesignError, setLlmDesignError] = useState<string | null>(null)

  // 模型列表状态
  const [availableModels, setAvailableModels] = useState<OpenAIModel[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [modelsError, setModelsError] = useState<string | null>(null)

  const [isOpenModelParams, setIsOpenModelParams] = useState(false)

  // 获取可用模型列表
  useEffect(() => {
    async function fetchModels() {
      setModelsLoading(true)
      setModelsError(null)
      try {
        const backendUrl = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/models`
        const response = await fetch(backendUrl)
        if (!response.ok) {
          throw new Error("Failed to fetch models")
        }
        const data: ModelsResponse = await response.json()
        setAvailableModels(data.data || [])
        console.log("获取到的模型列表:", data.data)
      } catch (error) {
        console.error("Error fetching models:", error)
        setModelsError("Failed to load models. Using defaults.")
        // 设置默认模型
        setAvailableModels([
          { id: "openai_official/chatgpt-4o-latest", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4o", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4o-mini", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-4-turbo", object: "model", created: 0, owned_by: "openai" },
          { id: "gpt-3.5-turbo", object: "model", created: 0, owned_by: "openai" },
        ])
      } finally {
        setModelsLoading(false)
      }
    }
    fetchModels()
  }, [])

  // Effect to initialize designer sub-options when mode changes
  useEffect(() => {
    setDesignerConfig((prevConfig) => {
      const newConfig = { ...prevConfig }
      if (prevConfig.type === "Human") {
        if (prevConfig.patternMode === "Visual" && !prevConfig.symmetryType) {
          newConfig.symmetryType = "Left-Right"
        } else if (prevConfig.patternMode === "Algorithmic" && prevConfig.shiftStep === undefined) {
          newConfig.shiftStep = 1
        }
      } else if (prevConfig.type === "LLM" && !prevConfig.llmModel) {
        // 设置默认模型为openai_official/chatgpt-4o-latest
        newConfig.llmModel = "openai_official/chatgpt-4o-latest"
      }
      return newConfig
    })
  }, [designerConfig.patternMode, designerConfig.type])

  const handleNextStep = () => {
    setLlmDesignError(null)
    if (step === "basic") setStep("designer")
    else if (step === "designer") {
      if (designerConfig.type === "Human" && designerConfig.patternMode === "Custom" && !designerConfig.customPattern) {
        setStep("customPatternEditor")
      } else {
        setStep("players")
      }
    } else if (step === "players") {
      // Ensure defaults are set if user hasn't interacted with sub-options
      const finalDesignerConfig = { ...designerConfig }
      if (finalDesignerConfig.type === "Human") {
        if (finalDesignerConfig.patternMode === "Visual" && !finalDesignerConfig.symmetryType) {
          finalDesignerConfig.symmetryType = "Left-Right"
        }
        if (finalDesignerConfig.patternMode === "Algorithmic" && finalDesignerConfig.shiftStep === undefined) {
          finalDesignerConfig.shiftStep = 1
        }
      } else if (finalDesignerConfig.type === "LLM" && !finalDesignerConfig.llmModel) {
        finalDesignerConfig.llmModel = "openai_official/chatgpt-4o-latest"
      }

      // 确保所有LLM玩家都有默认模型
      const finalPlayers = players.map((player) => ({
        ...player,
        llmModel: player.type === "LLM" && !player.llmModel ? "openai_official/chatgpt-4o-latest" : player.llmModel,
      }))

      onSetupComplete({ baseSettings, designer: finalDesignerConfig, players: finalPlayers })
    }
  }

  const handlePrevStep = () => {
    setLlmDesignError(null)
    if (step === "players") setStep("designer")
    else if (step === "designer" || step === "customPatternEditor") setStep("basic")
  }

  const handleCustomPatternSave = (pattern: Grid) => {
    setDesignerConfig((prev) => ({ ...prev, customPattern: pattern }))
    setStep("players")
  }

  const handleAddPlayer = () => {
    const newPlayerId = `player${players.length + 1}`
    setPlayers([...players, { id: newPlayerId, name: `Player ${players.length + 1}`, type: "Human" }])
  }

  const handleRemovePlayer = (id: string) => {
    if (players.length > 1) {
      setPlayers(players.filter((p) => p.id !== id))
    }
  }

  const handlePlayerChange = (id: string, field: keyof PlayerConfig, value: any) => {
    setPlayers(
      players.map((p) => {
        if (p.id !== id) return p
        const updatedPlayer = { ...p, [field]: value }
        if (field === "type") {
          if (value === "Human") {
            updatedPlayer.llmModel = undefined
            updatedPlayer.llmModelParams = undefined
          } else if (value === "LLM" && !updatedPlayer.llmModel) {
            updatedPlayer.llmModel = "openai_official/chatgpt-4o-latest"
          }
        }
        return updatedPlayer
      }),
    )
  }

  const handleDesignerTypeChange = (newType: "Human" | "LLM") => {
    setDesignerConfig((prev) => ({
      ...prev,
      type: newType,
      // Reset mode-specific options when type changes
      patternMode: newType === "Human" ? "Random" : undefined,
      symmetryType: undefined,
      shiftStep: undefined,
      customPattern: undefined,
      llmModel: newType === "LLM" ? "openai_official/chatgpt-4o-latest" : undefined,
      llmModelParams: newType === "LLM" ? prev.llmModelParams : undefined,
      llmPrompt: newType === "LLM" ? prev.llmPrompt : undefined,
      llmDesignedPattern: undefined,
    }))
    setLlmDesignError(null)
  }

  const handlePatternModeChange = (newMode: NonNullable<DesignerConfig["patternMode"]>) => {
    setDesignerConfig((prevConfig) => {
      const updatedConfig: DesignerConfig = {
        ...prevConfig,
        patternMode: newMode,
        customPattern: undefined, // Reset custom pattern
      }

      if (newMode === "Visual") {
        updatedConfig.symmetryType = prevConfig.symmetryType || "Left-Right"
        updatedConfig.shiftStep = undefined
      } else if (newMode === "Algorithmic") {
        updatedConfig.shiftStep = prevConfig.shiftStep || 1
        updatedConfig.symmetryType = undefined
      } else {
        // Random or Custom
        updatedConfig.symmetryType = undefined
        updatedConfig.shiftStep = undefined
      }
      return updatedConfig
    })
  }

  const handleRequestLLMPattern = async () => {
    setIsLLMDesigning(true)
    setLlmDesignError(null)
    setDesignerConfig((prev) => ({ ...prev, llmDesignedPattern: undefined }))

    const backendUrl = `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/design-pattern`

    try {
      const response = await fetch(backendUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gridSize: baseSettings.gridSize,
          numSymbols: baseSettings.numSymbols,
          llmModel: designerConfig.llmModel,
          llmModelParams: designerConfig.llmModelParams,
          prompt: designerConfig.llmPrompt,
        }),
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(
          errorData.error || errorData.detail || `LLM pattern design API request failed with status ${response.status}`,
        )
      }
      const data = await response.json()
      setDesignerConfig((prev) => ({ ...prev, llmDesignedPattern: data.pattern as Grid }))
    } catch (error) {
      console.error("Error designing pattern with LLM:", error)
      let detailedErrorMessage = "An unexpected error occurred."
      if (error instanceof TypeError && error.message.toLowerCase().includes("failed to fetch")) {
        detailedErrorMessage = `Network error: Could not connect to the backend server at ${backendUrl}. Please ensure it's running. (Details: ${error.message})`
      } else if (error instanceof Error) {
        detailedErrorMessage = error.message
      }
      setLlmDesignError(detailedErrorMessage)
    } finally {
      setIsLLMDesigning(false)
    }
  }

  const renderModelParamsConfig = (
    params: LLMModelParams | undefined,
    onChange: (params: LLMModelParams) => void,
    title = "Model Parameters",
    isDesigner = false, // 新增参数
  ) => {
    // 根据是否为设计师设置不同的默认值
    const defaultTemp = isDesigner ? 0.7 : 0.3

    return (
      <Collapsible open={isOpenModelParams} onOpenChange={setIsOpenModelParams}>
        <CollapsibleTrigger asChild>
          <Button variant="outline" size="sm" className="w-full justify-between bg-transparent">
            <div className="flex items-center gap-2">
              <Settings className="h-4 w-4" />
              {title}
            </div>
            <span className="text-xs text-slate-500">{isOpenModelParams ? "Hide" : "Show"} Advanced Settings</span>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-3 mt-3 p-3 border rounded-md bg-slate-50">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="temperature">Temperature (0-2)</Label>
              <Input
                id="temperature"
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={params?.temperature ?? ""}
                onChange={(e) =>
                  onChange({
                    ...params,
                    temperature: e.target.value ? Number.parseFloat(e.target.value) : undefined,
                  })
                }
                placeholder={`${defaultTemp} (default)`}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="maxCompletionTokens">Max Completion Tokens (1-4096)</Label>
              <Input
                id="maxCompletionTokens"
                type="number"
                min="1"
                max="4096"
                value={params?.maxCompletionTokens ?? ""}
                onChange={(e) =>
                  onChange({
                    ...params,
                    maxCompletionTokens: e.target.value ? Number.parseInt(e.target.value) : undefined,
                  })
                }
                placeholder="4096 (default)"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="topP">Top P (0-1)</Label>
              <Input
                id="topP"
                type="number"
                min="0"
                max="1"
                step="0.1"
                value={params?.topP ?? ""}
                onChange={(e) =>
                  onChange({
                    ...params,
                    topP: e.target.value ? Number.parseFloat(e.target.value) : undefined,
                  })
                }
                placeholder="1.0 (default)"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="frequencyPenalty">Frequency Penalty (-2 to 2)</Label>
              <Input
                id="frequencyPenalty"
                type="number"
                min="-2"
                max="2"
                step="0.1"
                value={params?.frequencyPenalty ?? ""}
                onChange={(e) =>
                  onChange({
                    ...params,
                    frequencyPenalty: e.target.value ? Number.parseFloat(e.target.value) : undefined,
                  })
                }
                placeholder="0 (default)"
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="presencePenalty">Presence Penalty (-2 to 2)</Label>
            <Input
              id="presencePenalty"
              type="number"
              min="-2"
              max="2"
              step="0.1"
              value={params?.presencePenalty ?? ""}
              onChange={(e) =>
                onChange({
                  ...params,
                  presencePenalty: e.target.value ? Number.parseFloat(e.target.value) : undefined,
                })
              }
              placeholder="0 (default)"
              className="w-full"
            />
          </div>
          <div className="text-xs text-slate-600 space-y-1">
            <p>
              <strong>Temperature:</strong> Controls randomness (0 = deterministic, 2 = very random)
            </p>
            <p>
              <strong>Max Completion Tokens:</strong> Maximum response length
            </p>
            <p>
              <strong>Top P:</strong> Nucleus sampling parameter
            </p>
            <p>
              <strong>Frequency/Presence Penalty:</strong> Reduce repetition
            </p>
            {isDesigner && (
              <p className="text-blue-600">
                <strong>Note:</strong> Designer uses higher default temperature (0.7) for more creativity
              </p>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    )
  }

  const renderModelSelect = (
    value: string | undefined,
    onChange: (value: string) => void,
    label: string,
    params?: LLMModelParams,
    onParamsChange?: (params: LLMModelParams) => void,
    isDesigner = false, // 新增参数
  ) => {
    return (
      <div className="space-y-3">
        <div className="space-y-2">
          <Label>{label}</Label>
          {modelsLoading ? (
            <div className="flex items-center space-x-2 p-2 border rounded-md">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm text-slate-600">Loading models...</span>
            </div>
          ) : (
            <Select value={value || "openai_official/chatgpt-4o-latest"} onValueChange={onChange}>
              <SelectTrigger>
                <SelectValue placeholder="Select LLM Model" />
              </SelectTrigger>
              <SelectContent>
                {availableModels.map((model) => (
                  <SelectItem key={model.id} value={model.id}>
                    {model.id}
                    {model.id === "openai_official/chatgpt-4o-latest" && (
                      <span className="ml-2 text-xs text-green-600">(Recommended)</span>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {modelsError && <p className="text-xs text-amber-600">{modelsError}</p>}
        </div>

        {onParamsChange && renderModelParamsConfig(params, onParamsChange, "Advanced Model Settings", isDesigner)}
      </div>
    )
  }

  const renderStepContent = () => {
    if (step === "customPatternEditor") {
      return (
        <CustomPatternEditor
          gridSize={baseSettings.gridSize}
          symbols={allSymbols.slice(0, baseSettings.numSymbols)}
          onSave={handleCustomPatternSave}
          onCancel={() => setStep("designer")}
        />
      )
    }
    return (
      <Card className="w-full max-w-2xl">
        <CardHeader>
          <CardTitle>Game Setup: {step.charAt(0).toUpperCase() + step.slice(1)}</CardTitle>
          <CardDescription>
            {step === "basic" && "Set the basic grid and symbol configuration."}
            {step === "designer" && "Configure the pattern designer (Human or LLM)."}
            {step === "players" && "Add and configure players for the game."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {step === "basic" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="gridSize">Grid Size (3-8)</Label>
                <Input
                  id="gridSize"
                  type="number"
                  min="3"
                  max="8"
                  value={baseSettings.gridSize}
                  onChange={(e) => setBaseSettings((s) => ({ ...s, gridSize: Number.parseInt(e.target.value) }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="numSymbols">Number of Symbols (2-{allSymbols.length})</Label>
                <Input
                  id="numSymbols"
                  type="number"
                  min="2"
                  max={allSymbols.length}
                  value={baseSettings.numSymbols}
                  onChange={(e) => setBaseSettings((s) => ({ ...s, numSymbols: Number.parseInt(e.target.value) }))}
                />
              </div>
            </>
          )}

          {step === "designer" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="designerType">Designer Type</Label>
                <Select
                  value={designerConfig.type}
                  onValueChange={(val) => handleDesignerTypeChange(val as "Human" | "LLM")}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Human">Human</SelectItem>
                    <SelectItem value="LLM">LLM AI</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {designerConfig.type === "Human" && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="patternMode">Pattern Generation Mode</Label>
                    <Select
                      value={designerConfig.patternMode || "Random"}
                      onValueChange={(val) =>
                        handlePatternModeChange(val as NonNullable<DesignerConfig["patternMode"]>)
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="Random">Random</SelectItem>
                        <SelectItem value="Visual">Visual (Symmetry)</SelectItem>
                        <SelectItem value="Algorithmic">Algorithmic (Shift)</SelectItem>
                        <SelectItem value="Custom">Custom (Define Manually)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {designerConfig.patternMode === "Visual" && (
                    <div className="space-y-2 pl-4 border-l-2">
                      <Label htmlFor="symmetryType">Symmetry Type</Label>
                      <Select
                        value={designerConfig.symmetryType || "Left-Right"}
                        onValueChange={(val) => setDesignerConfig((s) => ({ ...s, symmetryType: val as any }))}
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
                  {designerConfig.patternMode === "Algorithmic" && (
                    <div className="space-y-2 pl-4 border-l-2">
                      <Label htmlFor="shiftStep">Shift Step (1-{Math.max(1, baseSettings.numSymbols - 1)})</Label>
                      <Input
                        type="number"
                        min="1"
                        max={Math.max(1, baseSettings.numSymbols - 1)}
                        value={designerConfig.shiftStep || 1}
                        onChange={(e) =>
                          setDesignerConfig((s) => ({ ...s, shiftStep: Number.parseInt(e.target.value) }))
                        }
                      />
                    </div>
                  )}
                </>
              )}

              {designerConfig.type === "LLM" && (
                <>
                  {renderModelSelect(
                    designerConfig.llmModel,
                    (val) => setDesignerConfig((s) => ({ ...s, llmModel: val })),
                    "LLM Model",
                    designerConfig.llmModelParams,
                    (params) => setDesignerConfig((s) => ({ ...s, llmModelParams: params })),
                    true, // 新增：标识这是设计师
                  )}
                  <div className="space-y-2">
                    <Label htmlFor="llmPromptDesigner">
                      Optional: LLM Design Prompt (e.g., "Create a complex symmetrical pattern")
                    </Label>
                    <Textarea
                      id="llmPromptDesigner"
                      value={designerConfig.llmPrompt || ""}
                      onChange={(e) => setDesignerConfig((s) => ({ ...s, llmPrompt: e.target.value }))}
                      placeholder="e.g., 'a pattern with rotational symmetry' or 'a pattern that forms a spiral'"
                    />
                  </div>
                  <Button onClick={handleRequestLLMPattern} disabled={isLLMDesigning || !designerConfig.llmModel}>
                    {isLLMDesigning ? "Designing with LLM..." : "Request LLM Design Pattern"}
                  </Button>
                  {isLLMDesigning && <p className="text-sm text-slate-500 mt-2">LLM is thinking... please wait.</p>}
                  {llmDesignError && (
                    <Alert variant="destructive" className="mt-4">
                      <AlertTriangle className="h-4 w-4" />
                      <AlertDescription>{llmDesignError}</AlertDescription>
                    </Alert>
                  )}
                  {designerConfig.llmDesignedPattern && !llmDesignError && (
                    <div className="mt-4">
                      <Label>LLM Designed Pattern Preview:</Label>
                      <GameBoard
                        grid={designerConfig.llmDesignedPattern}
                        gridSize={baseSettings.gridSize}
                        symbolsInUse={allSymbols.slice(0, baseSettings.numSymbols)}
                        onCellClick={() => {}}
                        selectedCells={[]}
                        queriedCells={[]}
                        isGuessing={false}
                        finalGuess={null}
                        masterPattern={designerConfig.llmDesignedPattern}
                        isGameOver={true}
                        readOnly={true}
                      />
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {step === "players" && (
            <>
              {players.map((player, index) => (
                <Card key={player.id} className="p-4 space-y-3">
                  <div className="flex justify-between items-center">
                    <Label className="text-lg font-semibold">Player {index + 1}</Label>
                    {players.length > 1 && (
                      <Button variant="ghost" size="icon" onClick={() => handleRemovePlayer(player.id)}>
                        <Trash2 className="h-4 w-4 text-red-500" />
                      </Button>
                    )}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor={`playerName-${player.id}`}>Name</Label>
                    <Input
                      id={`playerName-${player.id}`}
                      value={player.name}
                      onChange={(e) => handlePlayerChange(player.id, "name", e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor={`playerType-${player.id}`}>Type</Label>
                    <Select value={player.type} onValueChange={(val) => handlePlayerChange(player.id, "type", val)}>
                      <SelectTrigger id={`playerType-${player.id}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="Human">
                          <User className="inline mr-2 h-4 w-4" /> Human
                        </SelectItem>
                        <SelectItem value="LLM">
                          <Bot className="inline mr-2 h-4 w-4" /> LLM AI
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {player.type === "LLM" && (
                    <div className="space-y-3 pl-4 border-l-2">
                      {renderModelSelect(
                        player.llmModel,
                        (val) => handlePlayerChange(player.id, "llmModel", val),
                        "LLM Model",
                        player.llmModelParams,
                        (params) => handlePlayerChange(player.id, "llmModelParams", params),
                      )}
                    </div>
                  )}
                </Card>
              ))}
              <Button variant="outline" onClick={handleAddPlayer} className="w-full bg-transparent">
                <PlusCircle className="mr-2 h-4 w-4" /> Add Player
              </Button>
            </>
          )}
        </CardContent>
        <CardFooter className="flex justify-between pt-6">
          <Button variant="outline" onClick={handlePrevStep} disabled={step === "basic"}>
            Previous
          </Button>
          <Button
            onClick={handleNextStep}
            disabled={
              (step === "designer" &&
                designerConfig.type === "LLM" &&
                (!designerConfig.llmDesignedPattern || isLLMDesigning)) ||
              isLLMDesigning
            }
          >
            {step === "players" ? "Finish Setup & Start Game" : "Next"}
          </Button>
        </CardFooter>
      </Card>
    )
  }

  return (
    <div className="min-h-screen bg-slate-100 flex flex-col items-center justify-center p-4">{renderStepContent()}</div>
  )
}
