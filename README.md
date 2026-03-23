# SENTINEL-8004
### Autonomous Self-Correcting Trading Agent

> An autonomous, three-layered financial agent that analyzes markets, enforces strict risk rules, and autonomously optimizes its own strategy after every trade — using ERC-8004 as the trust layer and Kraken CLI as the execution layer.

---

## Table of Contents

- [Overview](#overview)
- [Architecture: The Triple-Helix Logic](#architecture-the-triple-helix-logic)
- [Directory Structure](#directory-structure)
- [The Self-Correction Loop](#the-self-correction-loop)
- [Data Sources](#data-sources)
- [Bootstrap & Simulation Scripts](#bootstrap--simulation-scripts)
- [Adversarial Testing (Red Teaming)](#adversarial-testing-red-teaming)
- [ERC-8004 Trust Layer](#erc-8004-trust-layer)
- [Tech Stack](#tech-stack)
- [Setup & Installation](#setup--installation)
- [Roadmap](#roadmap)

---

## Overview

SENTINEL-8004 is not just a trading bot. It is a multi-agent system built around three core principles:

1. **Memory** — It remembers past market cycles and its own trade history via a RAG (Retrieval-Augmented Generation) layer.
2. **Safety** — Hard, deterministic risk rules run independently of any LLM, acting as circuit breakers.
3. **Self-Improvement** — After every trade, the system audits its own performance and autonomously updates its config and prompts.

All optimization actions are signed on-chain via the **ERC-8004** protocol, providing a verifiable audit trail of every strategic update the agent makes to itself.

---

## Architecture: The Triple-Helix Logic

```
┌─────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR                        │
│              (orchestrator.py — The Brain)               │
└──────────────┬──────────────────┬────────────────────────┘
               │                  │                  │
     ┌─────────▼──────┐  ┌────────▼───────┐  ┌──────▼──────────┐
     │  MEMORY LAYER  │  │  SAFETY LAYER  │  │ OPTIMIZATION    │
     │  (RAG Engine)  │  │ (Symbolic      │  │ LAYER           │
     │                │  │  Logic)        │  │ (Evaluator-     │
     │ - Past market  │  │                │  │  Optimizer)     │
     │   cycles       │  │ - Hard limits  │  │                 │
     │ - Own trade    │  │ - Circuit      │  │ - Post-trade    │
     │   history      │  │   breakers     │  │   analysis      │
     │ - Crisis       │  │ - Stop-loss    │  │ - Auto-updates  │
     │   scenarios    │  │   enforcement  │  │   config/prompts│
     └───────┬────────┘  └───────┬────────┘  └──────┬──────────┘
             │                   │                   │
             └───────────────────▼───────────────────┘
                         ┌───────────────┐
                         │  EXECUTION    │
                         │  Kraken CLI   │
                         └───────┬───────┘
                                 │
                         ┌───────▼───────┐
                         │   ERC-8004    │
                         │  On-Chain Log │
                         └───────────────┘
```

### Layer 1 — Memory (RAG)

The RAG layer stores and retrieves:
- Historical market cycles and price patterns
- The agent's own past trade logs
- LLM-generated "lessons learned" from crisis scenarios

When the Strategy Agent faces a new market condition, it queries the RAG store: *"What happened the last time volatility spiked like this?"*

### Layer 2 — Safety (Symbolic Logic)

Python-based deterministic rules that operate completely **independently of any LLM**. These never hallucinate and cannot be overridden by the strategy layer:

| Rule | Default Value |
|------|--------------|
| Max Daily Drawdown | 5% |
| Max Position Size | 2% of balance |
| Stop-Loss Floor | Configurable via `risk_policy.yaml` |
| Min Balance Check | Before every order |

### Layer 3 — Optimization (Evaluator-Optimizer)

After each trade closes, the Auditor Agent:
1. Pulls the trade log from the RAG store
2. Identifies the root cause of profit or loss
3. Updates `config/risk_policy.yaml` or `src/prompts/` autonomously
4. Signs the update on-chain via ERC-8004

---

## Directory Structure

```
sentinel-8004/
│
├── config/                        # Dynamic Control Center
│   ├── risk_policy.yaml           # Hard limits (auto-updated by Auditor)
│   └── model_routing.yaml         # Claude-3.5 for strategy, GPT-4o for auditing
│
├── src/
│   ├── core/                      # Engine Room
│   │   ├── orchestrator.py        # Analyze → Approve → Execute → Learn loop
│   │   ├── kraken_worker.py       # Kraken CLI wrapper (tool interface)
│   │   └── erc8004_client.py      # On-chain signing of trade logic & corrections
│   │
│   ├── agents/                    # The Multi-Agent Team
│   │   ├── strategy_agent.py      # Generates trade signals using RAG
│   │   ├── risk_manager.py        # Symbolic logic gate (veto or approve)
│   │   └── auditor_agent.py       # PnL analysis + self-correction trigger
│   │
│   ├── rag/                       # Market & Transaction Memory
│   │   ├── vector_store.py        # Chroma / Pinecone interface
│   │   └── retriever.py           # Semantic search over trade history & news
│   │
│   └── processing/                # Data Pipeline
│       ├── cleaner.py             # Normalizes raw Kraken CSV / API data
│       └── embedder.py            # Converts cleaned data into RAG-ready chunks
│
├── scripts/
│   ├── bootstrap_memory.py        # Seeds RAG with synthetic lessons from historical data
│   └── simulator.py               # Paper trading: runs agent over historical scenarios
│
├── data/
│   ├── historical/                # Kraken OHLCV CSVs
│   └── synthetic/                 # LLM-generated edge-case scenarios
│
├── tests/
│   └── red_team/
│       └── adversarial_agent.py   # Hacker Agent for stress-testing Risk Manager
│
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## The Self-Correction Loop

This is the core innovation of SENTINEL-8004. Every trade triggers a full feedback cycle:

```
1. OBSERVE
   └─ Strategy Agent pulls live data from Kraken
   └─ Queries RAG: "Similar patterns in the past?"

2. HYPOTHESIZE
   └─ "BTC/USD bullish signal detected — recommend 1% allocation long"

3. GATE (Risk Manager)
   └─ Checks: daily loss limit, position size, balance floor
   └─ Decision: APPROVE or VETO

4. EXECUTE
   └─ Order sent via Kraken CLI
   └─ Trade ID logged to RAG

5. AUDIT (Self-Correction)
   └─ Trade closes → Auditor Agent activates
   └─ If loss detected:
       a. Retrieve trade context from RAG
       b. Identify failure reason (e.g. "stop-loss too tight, hit noise")
       c. Update config/risk_policy.yaml: stop_loss_pct 1.0% → 1.5%
       d. Log correction to RAG as "Lesson #N"

6. TRUST SIGNAL
   └─ Correction event signed on-chain via ERC-8004
   └─ Tag: "Strategy Optimized | stop_loss_pct updated"
```

---

## Data Sources

| Data Type | Source | Purpose |
|-----------|--------|---------|
| Historical OHLCV | Kraken Public API / CSV | Seed RAG with market memory |
| Synthetic Scenarios | LLM-generated | Trigger self-correction during simulation |
| Live Signals | Kraken WebSocket | Real-time execution |
| News Feed | External (optional) | Sentiment enrichment for RAG |

---

## Bootstrap & Simulation Scripts

### `scripts/bootstrap_memory.py` — Build Synthetic Memory

Seeds the RAG vector store with lessons derived from historical crisis events before the agent ever trades live.

**What it does:**
1. Loads historical OHLCV CSV from `data/historical/`
2. Detects significant events (e.g. drops > 10% in a single candle)
3. Prompts the Auditor LLM: *"It's March 12, 2024. Price dropped 15%, RSI at 20. What should have been done?"*
4. Stores the response in the vector store as a tagged lesson

**Run:**
```bash
python scripts/bootstrap_memory.py --source data/historical/BTCUSD_2023_2024.csv
```

---

### `scripts/simulator.py` — Digital Twin (Paper Trading)

Runs the full agent stack over historical data in simulation mode — no real money, full self-correction behavior.

**What it does:**
1. Reads historical data row-by-row (1-minute candles)
2. Feeds each tick to `orchestrator.py` as if it were live
3. Maintains a virtual balance, executes paper orders
4. Triggers `auditor_agent.py` after each closed position
5. All config updates are written to `config/risk_policy.yaml`

**Run:**
```bash
python scripts/simulator.py --scenario flash_crash --capital 10000
```

**Built-in scenario presets:**
- `flash_crash` — Sudden -20% drop within 5 minutes
- `bull_pump` — Rapid +30% spike with high volume
- `sideways` — Low-volatility chop for 48 hours
- `random_100` — 100 randomly sampled 24h windows from historical data

**Result:** After simulation, your `config/risk_policy.yaml` will reflect the agent's learned optimal parameters — with on-chain proof of each update.

---

## Adversarial Testing (Red Teaming)

Located in `tests/red_team/adversarial_agent.py`.

A "Hacker Agent" is spawned to attempt to manipulate the Strategy Agent with false signals:

```
Hacker Agent: "Bitcoin will pump 20% in 10 minutes — buy everything NOW."
Risk Manager: Checks signal against Symbolic Logic rules.
              → If flagged: VETO + log manipulation attempt.
              → If NOT flagged: you have a rule gap. Update your codebase.
```

This is how you find weaknesses in the Safety Layer before going live.

**Run:**
```bash
python tests/red_team/adversarial_agent.py --target strategy_agent --rounds 50
```

---

## ERC-8004 Trust Layer

Every meaningful agent action is signed on-chain via the ERC-8004 protocol (Base / Ethereum), creating a public, verifiable audit trail.

| Event | On-Chain Tag |
|-------|-------------|
| Trade executed | `Trade Executed | pair=BTC/USD | size=0.01` |
| Risk veto | `Trade Vetoed | reason=daily_limit_exceeded` |
| Config update | `Strategy Optimized | param=stop_loss_pct | 1.0→1.5` |
| Simulation complete | `Simulation Complete | scenarios=100 | win_rate=67%` |

This transforms the agent from a black box into a **verifiably auditable system** — every optimization step has a timestamp and a hash.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Execution | Kraken CLI (Rust-based, AI-native) |
| Trust / Audit Trail | ERC-8004 (Base / Ethereum) |
| Orchestration | Python 3.11+ |
| LLM — Strategy | Claude 3.5 Sonnet |
| LLM — Auditing | GPT-4o |
| Vector Store | Chroma (local) / Pinecone (cloud) |
| Containerization | Docker + Docker Compose |
| Dependency Management | `pyproject.toml` (PEP 621) |

---

## Setup & Installation

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Kraken CLI installed and configured
- ERC-8004 wallet (Base network)
- Anthropic API key (Claude 3.5 Sonnet)
- OpenAI API key (GPT-4o for auditing)

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/your-org/sentinel-8004.git
cd sentinel-8004

# 2. Install dependencies
pip install -e .

# 3. Configure environment variables
cp .env.example .env
# Fill in: KRAKEN_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, ERC8004_WALLET_KEY

# 4. Start infrastructure (vector DB, etc.)
docker-compose up -d

# 5. Bootstrap agent memory with historical data
python scripts/bootstrap_memory.py

# 6. Run simulation before going live
python scripts/simulator.py --scenario random_100 --capital 10000

# 7. Review learned config
cat config/risk_policy.yaml

# 8. Start the live agent (paper trading mode)
python -m src.core.orchestrator --mode paper

# 9. Start the live agent (real trading — only after simulation validates)
python -m src.core.orchestrator --mode live
```

---

## Roadmap

- [x] Core orchestrator loop (Analyze → Approve → Execute → Learn)
- [x] RAG layer with Chroma vector store
- [x] Symbolic logic Risk Manager
- [x] Auditor Agent with config self-update
- [x] ERC-8004 on-chain signing
- [x] Bootstrap memory script
- [x] Paper trading simulator
- [ ] Red team adversarial testing suite
- [ ] Web dashboard for real-time monitoring
- [ ] Multi-exchange support (beyond Kraken)
- [ ] Cross-agent reputation scoring via ERC-8004

---

## Disclaimer

SENTINEL-8004 is experimental software. It interacts with live financial markets and blockchain networks. Use paper trading mode extensively before deploying real capital. The authors accept no responsibility for financial losses. Always comply with local regulations regarding automated trading.