import type { FC } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"

interface GameLogProps {
  log: string[]
}

export const GameLog: FC<GameLogProps> = ({ log }) => {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Game Log</CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-48 w-full pr-4">
          <div className="space-y-2">
            {log
              .map((entry, index) => (
                <p key={index} className="text-sm text-slate-700 border-b border-slate-200 pb-1">
                  {entry}
                </p>
              ))
              .reverse()}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
