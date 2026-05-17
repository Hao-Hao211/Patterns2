# Patterns II — An Abductive-Reasoning Benchmark for LLMs

> *"PATTERNS allows, in fact requires, the player to formulate a hypothesis and then test its validity by experimentation. ... This process, of course, is what we call the scientific method."*
> — Sid Sackson, *A Gamut of Games*, 1969

**Patterns II** is a research platform that turns Sid Sackson's pencil-and-paper game *Patterns* into a reproducible benchmark for evaluating **abductive reasoning under active observation** in large language models. It runs the game as a strict protocol with explicit actions, validates every model response, logs full game traces, and produces leaderboards that can be regenerated from stored data.

The system supports three workflows:

| Workflow | Purpose |
| --- | --- |
| **Single-game play** | Configure and run one game with human and/or LLM participants for inspection, demonstration, and qualitative debugging. |
| **Batch test-set evaluation** | Define model panels and game conditions, run repeated games at scale, and inspect stored histories and leaderboards. This drives the empirical experiments in the dissertation. |
| **Remote human testing** | Invite human participants through join tokens and participant-specific URLs to collect reference data without account management overhead. |

🌐 **Live deployment:** <https://haozhang.site>
📦 **Repository:** <https://github.com/Hao-Hao211/Patterns2>

<p align="center">
  <img src="docs/images/ch1_game_overview.png" alt="Patterns II benchmark overview" width="800"/>
</p>

---

## Table of Contents

1. [Architecture](#architecture)
2. [Tech Stack](#tech-stack)
3. [Repository Layout](#repository-layout)
4. [Prerequisites](#prerequisites)
5. [Environment Variables](#environment-variables)
6. [Local Development](#local-development)
7. [Production Deployment](#production-deployment)
8. [User Guide](#user-guide)
   - [Single-Game Play](#single-game-play)
   - [Batch Test-Set Evaluation](#batch-test-set-evaluation)
   - [Remote Human Testing](#remote-human-testing)
9. [Data & Reproducibility](#data--reproducibility)
10. [Citation](#citation)
11. [Acknowledgements](#acknowledgements)

---

## Architecture

<p align="center">
  <img src="docs/images/ch3_system_architecture.png" alt="System architecture" width="900"/>
</p>

The platform follows a clean three-tier architecture:

- **Next.js / React frontend** — Game-setup wizard, live playing area, test-set monitoring, history inspection, join and resume flows for human participants, and leaderboard pages. The frontend communicates with the backend purely through JSON HTTP requests.
- **FastAPI / Python backend** — Defines all request/response contracts as Pydantic models. The orchestration layer resolves pattern sources, drives Scientist turns, manages human sessions, and expands batch test sets. The LLM pipeline routes every model call through **OpenRouter**, records token usage, validates responses against the JSON schema, and feeds parsed actions or patterns back into the game engine.
- **PostgreSQL persistence** — Stores both benchmark configuration and game-level evidence. Three tables are central: `test_sets` (test-set configuration, status, join tokens, player sessions), `game_analytics` (per-participant per-game records for all later analysis), and `leaderboards` (cached summaries for fast inspection).

Two execution paths are central to every run:

1. **Designer path** — A setup request → pattern-source resolution → Designer prompt build → model call → validated pattern insertion into the game.
2. **Scientist path** — Playing area or batch runner → current Scientist prompt build → model call → validated action → grid update on `observe`, score computation on `guess` or `give_up`.

---

## Tech Stack

| Layer | Stack |
| --- | --- |
| Frontend | Next.js 15 · React 19 · TypeScript · Tailwind CSS · Radix UI · lucide-react |
| Backend | Python 3.11+ · FastAPI · Uvicorn · Pydantic · `httpx` · `asyncpg` |
| Model access | OpenRouter (single endpoint for every model provider) |
| Database | PostgreSQL (Neon in production) |
| Rating systems | `trueskill` library + custom ELO implementation |
| Hosting | Vercel (frontend) · Render (backend) · Neon (PostgreSQL) |

---

## Repository Layout

```
.
├── app/                          # Next.js App Router pages
│   ├── page.tsx                  # Landing screen
│   ├── history/                  # Stored game histories
│   ├── join/[token]/             # Remote human join + play page
│   ├── leaderboard/              # Aggregate leaderboard
│   └── test-sets/                # Test-set dashboard + create wizard + executor
├── components/                   # React UI components
│   ├── welcome-screen.tsx
│   ├── game-setup-wizard.tsx
│   ├── playing-area.tsx
│   ├── game-board.tsx
│   ├── controls.tsx
│   ├── designer-dashboard.tsx
│   └── ui/                       # shadcn/ui primitives
├── types/game-types.ts           # Shared TS types between frontend and API
├── backend/
│   ├── main.py                   # FastAPI app + all endpoints + orchestration
│   ├── models.py                 # Pydantic request/response models
│   ├── llm_client.py             # Thin async wrapper around OpenRouter
│   ├── prompt_manager.py         # Prompt templating engine
│   ├── prompts/                  # Current prompt templates (.txt)
│   ├── scoring.py                # Scientist + Designer scoring formulas
│   ├── leaderboard.py            # Leaderboard aggregation + DB schema
│   ├── init_db.py                # Initial DB bootstrap
│   └── requirements.txt
├── scripts/schema.sql            # SQL schema (legacy + reference)
└── docs/images/                  # System-manual screenshots used in this README
```

---

## Prerequisites

- **Node.js 18+** (Node 20 recommended) and **pnpm** (or npm) for the frontend
- **Python 3.11+** with `pip` for the backend
- **PostgreSQL 14+** (a managed instance such as Neon works out of the box)
- An **OpenRouter API key** — every model call is routed through OpenRouter, so a single key gives access to OpenAI, Anthropic, Google, Meta, xAI, DeepSeek, and others

---

## Environment Variables

Two `.env` files are used. **Never commit them.** Copy from `.env.example` if provided, or create them by hand.

### Frontend — `.env.local` (repo root)

```bash
# URL of the FastAPI backend, no trailing slash.
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Backend — `backend/.env`

```bash
# PostgreSQL connection string (Neon, RDS, or local instance)
DATABASE_URL=postgres://user:password@host:5432/dbname

# OpenRouter API key — all model traffic flows through OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...

# Comma-separated CORS allowlist for the frontend origin(s)
CORS_ORIGINS=http://localhost:3000,https://haozhang.site
```

A ready-to-copy template lives at `.env.example` and `backend/.env.example`.

---

## Local Development

### 1. Clone and install

```bash
git clone https://github.com/Hao-Hao211/Patterns2.git
cd Patterns2

# Frontend deps
pnpm install            # or: npm install

# Backend deps
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd ..
```

### 2. Initialise the database

The backend bootstraps the required tables (`test_sets`, `game_analytics`, `leaderboards`) at startup via its `lifespan` hook, so for a fresh database you only need to point `DATABASE_URL` at an empty PostgreSQL instance.

If you prefer to apply the legacy schema explicitly:

```bash
cd backend
python init_db.py
```

### 3. Start the backend

```bash
cd backend
source .venv/bin/activate
python main.py
# → Uvicorn listening on http://0.0.0.0:8000
```

The API exposes Swagger UI at <http://localhost:8000/docs>.

### 4. Start the frontend

```bash
# In a second terminal, at repo root
pnpm dev                # or: npm run dev
# → Next.js dev server on http://localhost:3000
```

Open <http://localhost:3000> and you should see the Patterns II landing screen.

---

## Production Deployment

The reference deployment uses **Vercel + Render + Neon**:

- **Vercel** picks up the `app/` directory automatically. Set `NEXT_PUBLIC_API_BASE_URL` in Vercel's project settings to the Render backend URL.
- **Render** runs `backend/` as a Python web service. Build command: `pip install -r requirements.txt`; start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`. All backend env vars (`DATABASE_URL`, `OPENROUTER_API_KEY`, `CORS_ORIGINS`) live in Render's dashboard.
- **Neon** provides serverless PostgreSQL; its connection string is fed into Render as `DATABASE_URL`.

The backend's `CORS_ORIGINS` must include the deployed frontend domain.

---

## User Guide

The user guide mirrors *Appendix F — System Manual* of the dissertation. Each subsection walks through one of the three platform workflows with annotated screenshots.

### Single-Game Play

Single-game play is the lightweight, exploratory mode. It is the right entry point for inspecting Scientist behaviour qualitatively or producing a quick demo.

#### 1. Landing screen → Start Game

The landing page exposes the four primary entry points: **Start Game**, **View Game History**, **Setup Batch Test**, and **View Leaderboard**.

<p align="center">
  <img src="docs/images/system_manual/sm01_start_screen.png" alt="Landing screen" width="500"/>
</p>

#### 2. Basic setup — grid size and symbols

The wizard first asks for grid size (3×3 up to 9×9) and the number of available symbols. The canonical 6×6 / 5-symbol setting reproduces Sackson's original game; other sizes support the grid-scaling experiments in Chapter 4.

<p align="center">
  <img src="docs/images/system_manual/sm02_basic_setup.png" alt="Basic setup" width="500"/>
</p>

#### 3. Designer configuration

The Designer can be a **human** (entering or selecting a pattern directly) or an **LLM** (choosing a model, generation parameters, and an optional custom design instruction). When an LLM Designer is used, the generated pattern is shown together with its rationale before the game starts so that the operator can inspect what was produced.

| LLM Designer | Human Designer |
| :---: | :---: |
| <img src="docs/images/system_manual/sm04_designer_setup_llm.png" alt="LLM Designer" width="380"/> | <img src="docs/images/system_manual/sm03_designer_setup_human.png" alt="Human Designer" width="380"/> |

#### 4. Scientist panel

Any number of Scientists can be added — humans, LLMs, or a mix.

<p align="center">
  <img src="docs/images/system_manual/sm05_player_setup.png" alt="Scientist setup" width="380"/>
</p>

#### 5. Gameplay

The gameplay view shows the current player's visible board, the action log, and a tabbed view of the other participants. LLM Scientists play automatically; a human Scientist can play in the same game by selecting cells to observe and later submitting a full-grid guess.

| LLM play in progress | Human cell selection before observation |
| :---: | :---: |
| <img src="docs/images/system_manual/sm06_gameplay_llm_progress.png" alt="LLM progress" width="430"/> | <img src="docs/images/system_manual/sm07_human_observe_selection.png" alt="Human observe" width="430"/> |

#### 6. Guess submission and feedback

When a human Scientist is ready to commit, **Guess** mode turns the board into an answer-entry interface. Clicking an unknown cell cycles through the available symbols. After submission, the result view overlays correctness feedback: **green** for correct guesses, **red** for incorrect, and **grey** for cells that were observed during play (and therefore score 0).

| Full-grid guess entry | Correctness overlay |
| :---: | :---: |
| <img src="docs/images/system_manual/sm08_human_guess_submission.png" alt="Guess submission" width="430"/> | <img src="docs/images/system_manual/sm09_human_game_finished.png" alt="Game finished" width="430"/> |

#### 7. Final scores

After every Scientist finishes, the Designer dashboard summarises final scores and Designer outcomes for the run.

<p align="center">
  <img src="docs/images/system_manual/sm10_final_scores_designer_dashboard.png" alt="Designer dashboard" width="380"/>
</p>

---

### Batch Test-Set Evaluation

Batch test-set evaluation is the workflow for **reproducible model experiments**. It is the operational basis for all empirical results reported in the dissertation.

#### 1. Test-set dashboard

The dashboard lists every stored test set with its status, progress, and links to inspection / cloning / execution. Cloning is the fastest way to reproduce a run with only a small change (e.g. a different model panel or repeat count).

<p align="center">
  <img src="docs/images/system_manual/sm11_test_set_dashboard.png" alt="Test-set dashboard" width="700"/>
</p>

#### 2. Basic metadata + designer rotation

A new test set begins with a name, description, and global settings. **Designer rotation** is the toggle that turns the test set into a dual-role evaluation: each selected participant is used as Designer in turn while the remaining panel plays as Scientists. This is the mode used for the Designer-role evaluation in Chapter 4.

| Basic info | Designer-rotation toggle |
| :---: | :---: |
| <img src="docs/images/system_manual/sm12_test_set_basic_info.png" alt="Basic info" width="430"/> | <img src="docs/images/system_manual/sm13_designer_rotation_settings.png" alt="Designer rotation" width="430"/> |

#### 3. Scientist panel

Multiple LLM participants can be added, each with a display name and an OpenRouter-compatible model identifier.

<p align="center">
  <img src="docs/images/system_manual/sm14_scientist_participant_setup.png" alt="Scientist panel" width="500"/>
</p>

#### 4. Per-model controls and history-assisted play

For each participant the wizard exposes standard generation controls (temperature, top-p, max tokens, frequency penalty). For models that support explicit reasoning controls, a **reasoning-effort** selector is also shown. The same section exposes optional multi-turn chat history and history-assisted play — used in the briefing-effect experiment.

| Model parameters and reasoning effort | Chat history and history-assisted play |
| :---: | :---: |
| <img src="docs/images/system_manual/sm15_model_parameters.png" alt="Model parameters" width="430"/> | <img src="docs/images/system_manual/sm16_history_assisted_options.png" alt="History options" width="430"/> |

#### 5. Game configurations

The game-configuration step defines one or more game conditions for the test set. Each condition specifies grid size, symbol set, repeat count, and **pattern-generation mode**: fixed-battery, a custom human-created pattern, a random generator, or an LLM Designer (Per-Game). The interface computes the total number of games implied by the chosen participants, patterns, repeats, and designer-rotation setting.

<p align="center">
  <img src="docs/images/system_manual/sm17_game_configurations.png" alt="Game configurations" width="900"/>
</p>

#### 6. Progress monitoring

Once a test set is started, the backend executes games and updates progress records. The dashboard shows real-time status while the run is active and later links back to the stored game histories, scores, and leaderboards.

<p align="center">
  <img src="docs/images/system_manual/sm18_test_set_progress_card.png" alt="Test-set progress" width="900"/>
</p>

---

### Remote Human Testing

Remote human testing extends the batch workflow to **external human participants** without requiring a public account system.

#### 1. Add human participants

In the participant step of the wizard, add human participants and assign each a display name. The presence of any human participant unlocks the remote-testing step that follows.

<p align="center">
  <img src="docs/images/system_manual/sm19_human_participant_setup.png" alt="Human participants" width="500"/>
</p>

#### 2. Remote-testing setup

When the test set contains human participants, a dedicated remote-testing step appears at the end of the wizard. It generates a session-level join token and a participant-facing URL.

<p align="center">
  <img src="docs/images/system_manual/sm20_remote_human_testing_setup.png" alt="Remote testing setup" width="600"/>
</p>

#### 3. Distribute the join link

The token identifies the human-testing session and binds each remote participant to the correct test set. From the same page the operator can monitor live participant status — who has joined, who is ready.

<p align="center">
  <img src="docs/images/system_manual/sm21_join_token_and_status.png" alt="Join token and status" width="600"/>
</p>

#### 4. Participant join flow

Each participant opens the URL (`/join/<TOKEN>`), enters a name, and lands in a waiting room until the required players are present.

| Join page | Waiting room |
| :---: | :---: |
| <img src="docs/images/system_manual/sm22_join_game_page.png" alt="Join page" width="430"/> | <img src="docs/images/system_manual/sm23_waiting_room.png" alt="Waiting room" width="430"/> |

#### 5. Remote gameplay with live leaderboard

Once the session starts, each participant enters the same game interface used in single-game play, with one addition: a **live leaderboard** showing every participant's progress and accumulated score.

<p align="center">
  <img src="docs/images/system_manual/sm24_remote_human_gameplay.png" alt="Remote gameplay" width="700"/>
</p>

#### 6. Final results

At the end of the assigned games each participant receives a final summary with total score, number of games completed, and final rank. Human runs are stored in the same backend schema as LLM runs, making the two directly comparable for later analysis.

<p align="center">
  <img src="docs/images/system_manual/sm25_remote_final_results.png" alt="Final results" width="500"/>
</p>

---

## Data & Reproducibility

The platform separates the benchmark run from later statistical analysis: leaderboards, figures, and experiment-specific summaries are derived from stored records rather than transient application state. As a result, the same raw records can support different analyses without re-running model calls.

| Stored record type | Examples | Purpose |
| --- | --- | --- |
| Game configuration | grid size, symbol set, pattern source, repeat index, role assignment | identifies the controlled condition |
| Participant metadata | human / model identifier, provider, model parameters, reasoning effort | enables runs to be compared and reproduced |
| Action process | turn number, action type, observed cells, raw response, parsed response, validation status | supports trace-level analysis |
| Outcome data | final guess, hidden pattern, score, give-up status, validation failures | drives leaderboard and case-study analysis |
| Execution data | token usage, cost summaries, timestamps, batch status | supports auditability and cost accounting |

Every game stores both the **hidden pattern** and the **submitted guess**, so Scientist scores can be re-derived from the score formula. Designer rotation also stores the full Scientist panel outcomes, so Designer scores can be recomputed from any of the candidate formulas explored in Appendix E.

CSV exports of per-game scores, observations, guesses, hidden patterns, model metadata, and token / cost data are available from the test-set dashboard.

---

## Citation

If you use this platform or its results, please cite the dissertation:

```bibtex
@mastersthesis{zhang2026patterns2,
  title  = {Patterns II: An Abductive-Reasoning Benchmark for Large Language Models},
  author = {Zhang, Hao},
  school = {University College London},
  year   = {2026},
  note   = {\url{https://github.com/Hao-Hao211/Patterns2}},
}
```

---

## Acknowledgements

Patterns II is based on Sid Sackson's *Patterns* (in *A Gamut of Games*, 1969) and the column by Martin Gardner that popularised it (*Scientific American*, 1969). The benchmark adaptation, system implementation, and empirical evaluation were carried out as part of the author's MSc Final-Year Project at University College London.

Model calls are routed through [OpenRouter](https://openrouter.ai). The TrueSkill rating implementation uses the official [`trueskill`](https://trueskill.org) Python library.
