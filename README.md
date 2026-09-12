# WaterfallHunter

Professional real-time crypto signal intelligence system with multi-source cascade verification, AI advisory (Ollama), and backtesting.

## Architecture

```
LBank API → Catalog (149 symbols) → Multi-Source Scanner → Cascade Intelligence
    → Entry Decision Engine → AI Advisory (Ollama) → Telegram + Dashboard
```

## Key Components

| Component | Description |
|-----------|-------------|
| Multi-Source Scanner | Real-time scanning of 149 symbols from LBank |
| Cascade Intelligence | Aggregates 8 independent data sources (PASS threshold: ≥4.0) |
| Entry Decision Engine | Produces ENTRY_READY / FORMING / LATE / NO_TRADE decisions |
| AI Advisory | Ollama (qwen2.5:1.5b) — observational only, no veto power |
| Backtester V2 | $100 capital, 30% max exposure, 3 positions, 4x-14x leverage |
| Risk Manager | Dynamic leverage based on score and risk profile |
| Telegram Bot | Signal alerts + /signals, /health, /top, /help commands |

## Decision Levels

| Level | Score | Cascade | Description |
|-------|-------|---------|-------------|
| ENTRY_READY | ≥70 | PASS | Ready to enter — Telegram alert sent |
| FORMING | 55-69 | PASS | Forming — visible on dashboard |
| NO_TRADE | <55 or FAIL | — | Conditions not met |
| LATE | — | — | Signal too late — do not chase |
| INVALIDATED | — | — | Structure broken |

## Backtest Results

| Metric | Value |
|--------|-------|
| Win Rate | 66.7% (4 wins, 1 loss, 1 timeout) |
| Initial Capital | $100 |
| Final Capital | $136.80 |
| Return | 36.8% |
| Profit Factor | 12.39 |
| Sharpe Ratio | 2.34 |
| Max Drawdown | 3.2% |
| Avg Leverage | 11.0x |

## Score Formula

```
readiness_score = (structure_score * 0.30) + (cascade_score * 0.25) +
                  (fundamental_score * 0.20) + (ai_advisory_score * 0.15) +
                  (execution_score * 0.10)
```

## AI Configuration

- **Model**: qwen2.5:1.5b (Ollama, CPU-only)
- **URL**: http://host.docker.internal:11434
- **Timeout**: 120s (CPU mode)
- **Provider**: Ollama only (no external APIs)

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Ollama installed on host with qwen2.5:1.5b model

### Deployment

```bash
# Clone
git clone https://github.com/cavack/WaterfallHunter.git
cd WaterfallHunter

# Configure environment
cp .env.example .env
# Edit .env with your Telegram token, Ollama URL, etc.

# Build and start
docker-compose up -d --build

# Or use the Makefile
make up
```

### Environment Variables

See `.env.example` for all required variables:
- `TELEGRAM_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — Telegram chat ID
- `OLLAMA_BASE_URL` — Ollama API URL (default: http://host.docker.internal:11434)
- `OLLAMA_MODEL` — Ollama model name (default: qwen2.5:1.5b)
- `COINGLASS_API_KEY` — Coinglass API key
- `BACKTESTER_INITIAL_CAPITAL` — Backtest capital (default: 100)
- `BACKTESTER_MAX_LEVERAGE` — Max leverage (default: 14)
- `BACKTESTER_MAX_EXPOSURE_PCT` — Max exposure % (default: 30)
- `BACKTESTER_MAX_POSITIONS` — Max simultaneous positions (default: 3)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System health check |
| `/api/candidates` | GET | Candidate list with signals |
| `/api/recent-signals` | GET | Recent signals |
| `/api/backtest/results` | GET | Backtester V2 results |
| `/api/ai-advisory?symbol=X` | GET | AI advisory for a symbol |
| `/api/fundamental?symbol=X` | GET | Fundamental score |

## Database

SQLite database with key tables:
- `lbank_signal_ledger` — 3,383 real signals with entry/SL/TP
- `lbank_signal_outcomes` — 2,573 real outcomes
- `entry_decision_events` — Decision records
- `bt_v2_trades` — Backtest trades
- `bt_v2_equity_curve` — Backtest equity curve

## Docker Services

| Container | Port | Description |
|-----------|------|-------------|
| waterfall-backend | 8000 (internal) | API + Scanner + Decision Engine |
| waterfall-frontend | 3000 | Next.js Dashboard |
| waterfall-watchdog | — | Health monitoring |
| waterfall-prometheus | 9090 | Metrics |
| waterfall-grafana | 3001 | Visualization |
| waterfallhunter-alertmanager | 9093 | Alert routing |

## License

See [LICENSE](LICENSE).
