# Mainline Agent

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 193 Passed (Offline)](https://img.shields.io/badge/tests-193%20passed%20offline-brightgreen.svg)](tests/)
[![Security: Read--Only & Local AI](https://img.shields.io/badge/security-read--only%20%7C%20local%20AI-success.svg)](#security--privacy-guarantees)

**Mainline Agent** is a privacy-first, read-only documentation drift monitoring tool. It continuously monitors codebases and pull requests to ensure that documentation, code examples, CLI arguments, and prose accurately reflect underlying implementation changes—powered by static AST checks and locally hosted open-weight LLMs via Ollama.

---

## Key Highlights & Security Guarantees

* **Strictly Read-Only:** GitHub interactions are restricted to read-only `GET` requests. The agent **never** writes comments, opens issues, modifies labels, commits code, or pushes changes.
* **100% Local AI:** Inference runs exclusively against a local loopback Ollama endpoint (e.g. `http://127.0.0.1:11434`). No source code, diffs, or prompts ever leave your machine.
* **No Secret Persistence:** Tokens (`GITHUB_TOKEN`) are held in memory only during active execution. They are never written to disk, saved in config files, or exported to subprocess environments.
* **Dual-Tier Verification:**
  1. *Deterministic Static Checks:* AST-based parameter verification, argparse defaults, Markdown code-fence evaluation, and signature drift detection.
  2. *Contradiction-Verified Local LLM:* Local models evaluate semantic prose drift, backed by an explicit contradiction-verification pass to eliminate false positives.

---

## Quick Start

### 1. Interactive Guided Setup (macOS / Linux)

On macOS, you can launch the guided setup wizard directly from Finder or your terminal:

```bash
bash setup.command
```

Or run via Python directly on any platform:

```bash
python3 guided_setup.py
```

The launcher:
1. Verifies Python 3.9+ availability.
2. Checks for or starts a local loopback Ollama server.
3. Benchmarks available hardware memory and configures the optimal open-weight model tier.
4. Allows you to choose between:
   - Single immediate analysis (preview once)
   - Continuous read-only monitoring
   - Setup configuration only

---

## CLI Usage

### Read-Only GitHub Monitoring

Monitor a target GitHub repository for documentation drift across PRs and main merges:

```bash
python3 -m mainline_agent github-watch \
  --repo OWNER/REPOSITORY \
  --config .mainline/config.json \
  --state .mainline/monitor/state.json \
  --out .mainline/monitor/logs \
  --once
```

* Remove `--once` to enable continuous polling (defaults to 60-second intervals; minimum 30s).
* Public repositories require no token. Private repositories only require a read-scoped token passed via `GITHUB_TOKEN` in your environment.

### Local Repository Scans

Scan a local Git repository without requiring internet access or GitHub APIs:

```bash
# Generate a test demo repository
python3 examples/make_demo.py .mainline/demo-repo

# Run static-only analysis
python3 -m mainline_agent scan --path .mainline/demo-repo \
  --no-llm --out .mainline/demo-report
```

* Exit code `0`: No drift or discrepancies detected in checked evidence.
* Exit code `1`: Drift or conflicts established with high confidence.
* Exit code `2`: Incomplete analysis, missing prerequisites, or configuration error.

### Historical Commit Replay

Evaluate drift at a specific historical commit before documentation fixes were committed:

```bash
python3 -m mainline_agent scan --path /path/to/repo \
  --base BEFORE_CODE_CHANGE_SHA \
  --at-main CODE_CHANGE_SHA \
  --out .mainline/history-evaluation
```

---

## Local Model Selection & Hardware Tiers

Mainline Agent probes your hardware (system RAM, Apple Silicon unified memory, and dedicated GPU VRAM) to select the appropriate local model tier:

| Hardware Capacity | Recommended Model | Quantization | Context Window |
|---|---|---|---|
| **16 GiB** Apple Silicon / 8–12 GiB VRAM | `qwen2.5-coder:7b` | Q4_K_M | 8,192 tokens |
| **24–32 GiB** Apple Silicon / 16–24 GiB VRAM | `qwen2.5-coder:14b` | Q4_K_M | 16,384 tokens |
| **48+ GiB** Apple Silicon / 32+ GiB VRAM | `qwen3-coder:30b` | Q4_K_M | 32,768 tokens |

To manually simulate a specific hardware environment (useful in headless or containerized setups):

```bash
python3 bootstrap.py --ram-gb 16 --unified --offline
```

---

## Output Architecture

All scan outputs are written strictly to your chosen local directory:

* **`maintainer.log`**: Human-readable, timestamped log containing exact documentation quotations, responsible source code references, patch summaries, coverage indicators, and findings.
* **`analysis.log`**: Structured JSON report representing the snapshot of the latest analysis run.

No remote comments or issues are ever created. Findings stay completely private for the maintainer.

---

## Detection Capabilities & Boundaries

### Supported Deterministic Checks
* **Python Signatures:** Top-level functions, argument additions/removals, default value alterations.
* **CLI Arguments:** `argparse` default declarations and help text sync.
* **Code Fences:** Executable examples in Markdown documentation tested against revised signatures.
* **Prose & Tables:** Explicit default values declared in tables and paragraphs.

### LLM Semantic Analysis & Safeguards
* **Grounding Enforcement:** LLM findings require unambiguous, verified line citations in current code and current documentation. Fabricated or hallucinatory citations trigger immediate rejection.
* **Contradiction Verification:** Semantic suggestions undergo a secondary automated contradiction check to ensure unchanged or already-consistent documentation is not falsely flagged.
* **Inspection Caps:** Default safeguards cap inspection to 30 code files, 200 documents, 40 model packets, and 100 KB per inspected file to keep local runs fast and predictable.

---

## Testing & Verification

The test suite is fully offline: outbound network sockets and DNS are disabled in the test harness, and all Git and GitHub operations use synthetic local fixtures.

```bash
# Run the complete offline test suite (193 tests)
python3 tests/run_offline.py

# Run with standard unittest runner
python3 -m unittest discover -s tests -v

# Run targeted monitor test module
python3 -m unittest tests.test_monitor -v
```

See [`VALIDATION.md`](VALIDATION.md) for detailed validation evidence across supported Python versions (3.9 through 3.12).

---

## Project Structure

```
mainline-agent/
├── mainline_agent/         # Core application package
│   ├── cli.py              # CLI entry point and argument parsing
│   ├── github.py           # Read-only GitHub snapshot & API client
│   ├── engine.py           # Analysis engine orchestrating checks & model
│   ├── checks.py           # Deterministic AST and signature checkers
│   ├── prose.py            # Prose and Markdown table parsers
│   ├── llm.py              # Loopback Ollama client & contradiction checks
│   ├── setup_wizard.py     # Interactive terminal setup wizard
│   ├── hardware.py         # Hardware probe & model tier recommendation
│   └── report.py           # Report and log generators
├── tests/                  # Complete offline test suite (193 tests)
├── examples/               # Example reports, synthetic fixtures & demo generator
├── .github/workflows/      # Automated GitHub Actions test CI
├── setup.command           # macOS double-clickable launcher
├── guided_setup.py         # Cross-platform guided setup entry point
├── bootstrap.py            # Standalone hardware & model verification script
├── pyproject.toml          # Packaging metadata
└── README.md
```

---

## License

This project is licensed under the [MIT License](LICENSE).
