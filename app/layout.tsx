import type React from "react"
import type { Metadata } from "next"
import { Inter } from "next/font/google"
import "./globals.css"

const inter = Inter({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Patterns II - Logic Puzzle Game",
  description:
    "Challenge your deductive skills and unravel hidden patterns. Play as a scientist, or design the ultimate puzzle for others to solve.",
  keywords: ["puzzle", "logic", "pattern", "game", "deduction"],
  authors: [{ name: "Hao Zhang" }],
  openGraph: {
    title: "Patterns II - Logic Puzzle Game",
    description: "Challenge your deductive skills and unravel hidden patterns.",
    type: "website",
  },
    generator: 'v0.dev'
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <head>
        <link rel="icon" href="/favicon.ico" />
      </head>
      <body className={inter.className}>{children}</body>
    </html>
  )
}
