# CNGN Trading Engine

Automated market-making engine for the cNGN stablecoin: cross-venue arbitrage
(CEX↔DEX and DEX↔DEX pipelines), Uniswap V4 liquidity provision, and a
real-time monitoring dashboard.

## Venues

| Venue | Type | Market | Notes |
| --- | --- | --- | --- |
| `quidax` | CEX (REST) | cNGN/USDT | Order-ladder market making + arb legs |
| `strails` | [StablesRail](https://docs.strails.co) FX orderbook (REST) | CNGN-USDC, settles on Base | Escrow settlement (~1–2.5 min observed); adapter emits executable prices (LP reference × 1∓spread) and resolves trades through a pending/half-open state machine — all verified against live fills in both directions |
| `uni-base` | Uniswap V4 (Base) | cNGN/USDC | Swaps + LP positions |
| `uni-bsc` | Uniswap V4 (BSC) | cNGN/USDT | Swaps + LP positions |
| `blockradar` | Wallet infrastructure | — | cNGN rates and transfers |

The route registry (`engine/arb/routing/route_registry.py`) is the single
source of truth for tradable directions; detection, sizing, and execution all
derive from it. Inventory is venue-local, and every venue self-registers when
its credentials are present in `.env`.

## Getting Started

### Prerequisites

- Python 3.11+
- Access to Base/BSC RPC endpoints
- API keys for venues (Quidax, StablesRail, Blockradar — each optional; a venue without keys is simply not started)

### Quick Start

```bash
git clone https://github.com/robertleifke/forex-maker.git
cd forex-maker

# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env — at minimum set QUIDAX_API_KEY for live CEX prices.
# For StablesRail set STRAILS_API_KEY + STRAILS_SMART_WALLET_ADDRESS
# (see .env.example for the IP-allowlist / SOCKS-proxy notes).

# Run the engine
python -m engine.main
```

## Local Checks

```bash
source .venv/bin/activate
python -m mypy engine --no-error-summary
python -m pytest -x -q --ignore=tests/test_dex_fork.py
python -m pytest -q tests/test_dex_fork.py -v
```

- CI runs the same strict `mypy` check and the default pytest suite on pull requests and pushes to `main`.
- `mypy` covers `engine/`, the production Python package, and intentionally ignores `tests/` and `dashboard/` because test doubles are looser by design and frontend code can be checked by its own toolchain.
- The default pytest run covers the fast local suite and intentionally skips `tests/test_dex_fork.py`, because those tests need Foundry's `anvil` plus RPC-backed fork access.

---

## Dashboard

A Next.js dashboard for real-time monitoring.

### Running the Dashboard

```bash
cd dashboard

# Install dependencies
npm install

# Development mode (with hot reload)
npm run dev

# Production build
npm run build
npm start
```

The dashboard will be available at `http://localhost:3000`.

### Configuration

Create `dashboard/.env.local`:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000/api
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
```

---

## Running with Docker

A `Dockerfile` and `docker-compose.yml` are provided. Before deploying, edit
`docker-compose.yml` and set `VIRTUAL_HOST` / `LETSENCRYPT_HOST` / `LETSENCRYPT_EMAIL`
to your own domain and email (they ship as placeholders). Then:

```bash
cp .env.example .env      # fill in your keys
docker compose up -d --build
```

`deploy/setup.sh` documents a full server deployment behind nginx-proxy + Let's Encrypt.

## Documentation

Design and operational docs live in [`dashboard/docs/`](dashboard/docs/):

- [Architecture](dashboard/docs/architecture.md)
- [Arbitrage](dashboard/docs/arbitrage/overview.md) — signal, execution, risk, post-trade
- [Liquidity provision](dashboard/docs/lp/overview.md) — price range, inventory
- [Runbook](dashboard/docs/runbook.md) — deployment and operations

## License

Released under the [MIT License](LICENSE).

## Disclaimer

This software is provided for educational and research purposes only. It executes
automated trades with real funds on live venues; running it can result in the loss
of some or all of your capital. It is not financial advice. Use at your own risk
the authors accept no liability for any losses. Test thoroughly on testnets/paper
before deploying with real money.
