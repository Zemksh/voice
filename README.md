# voice — local speaking practice

A private, local-first speaking-practice app. You get a topic of the day,
record a 60–90-second response on your phone, transfer the file to your
laptop, and select it in the app. The app transcribes it locally
(faster-whisper `small.en`, CPU, `int8`), lets you correct the transcript,
and returns rubric-based feedback and scores (relevance, organization,
support/examples, concision, 1–5 each) from a local Gemma model served by
Ollama.

**Privacy by design:** the backend binds to `127.0.0.1` only, the frontend
talks only to the local backend, all inference runs locally, and audio,
transcripts, and feedback never leave your laptop. There is no cloud
fallback of any kind.

## Where data and models live

All runtime data lives under `VOICE_DATA_DIR` (default
`~/.local/share/voice-practice`), **outside the source directory**:

- `practice.sqlite3` — sessions, transcripts, feedback, job state.
- `audio/<session-id>/` — the uploaded original file (`original`) and the
  FFmpeg-normalized mono 16 kHz WAV (`normalized.wav`). Deleted together
  with the session.

Models are an **explicit setup step** and are never downloaded by the app:

- Whisper `small.en`: run `scripts/prepare_model.py` once (network). It
  downloads Systran/faster-whisper-small.en (~250–300 MB) from Hugging Face
  into `WHISPER_MODEL_DIR` (default
  `~/.local/share/voice-practice/models/small.en`). After that, transcription
  uses local files only (`local_files_only=True`) and works offline.
- Gemma: install Ollama, then pull the exact model tag yourself, e.g.
  `ollama pull gemma3:1b`, and set `GEMMA_MODEL` to that same tag. The app
  refuses remote/cloud-backed models and falls back to nothing.

Target machine: Fedora Linux, Intel Core Ultra 9 185H, 32 GB RAM, CPU
inference only — no dedicated GPU required.

## Prerequisites

- Fedora Linux
- FFmpeg (with the phone-audio decoders for m4a/aac, mp3, wav, webm/opus)
  ```bash
  sudo dnf install ffmpeg
  ```
- Python 3.13 + venv
- Ollama (https://ollama.com), running locally on `127.0.0.1:11434`
  ```bash
  # as a user service on Fedora
  systemctl --user enable --now ollama
  ollama pull gemma3:1b        # or any Gemma-family tag you want to use
  ```
- Node 20+ and npm (frontend; only needed to build the frontend bundle)

## Setup

1. Create the venv and install runtime dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r backend/requirements-models.txt
   ```
2. Prepare the transcription model (explicit network step):
   ```bash
   python scripts/prepare_model.py
   ```
3. Configure the environment:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`. `GEMMA_MODEL` is **required** — set it to the exact Ollama
   model tag you pulled (for example `GEMMA_MODEL=gemma3:1b`). No default,
   no fallback, no guessing. Keep `VOICE_DATA_DIR` outside the source tree.
4. Build the frontend (served by the backend at `/`):
   ```bash
   cd frontend
   npm install
   npm run build        # produces frontend/dist
   cd ..
   ```
5. Start the backend (one worker only):
   ```bash
   source .venv/bin/activate
   uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```
6. Open the app:
   - Built frontend: http://127.0.0.1:8000/
   - Frontend dev server (Vite, proxies `/api` to the backend):
     ```bash
     cd frontend && npm run dev   # http://127.0.0.1:5173
     ```

## MOCK_MODE (UI development only)

Set `MOCK_MODE=true` in `.env`. Transcription and feedback are then simulated
and every mock session and every mock assessment is labeled **MOCK** in the
UI ("MOCK demonstration · no real assessment"). Audio conversion is still
real. Use this only to develop the UI when Ollama or the whisper model are
unavailable. Never present mock output as a real assessment.

## Daily workflow

1. **Home** — read today's topic (chosen from a local list by your local
   date) or enter a custom topic. Aim for 60–90 seconds. Pick up a recent
   attempt if you want.
2. **Upload** — drop or select the transferred recording (m4a, mp3, wav,
   webm; up to 30 MB, 3 minutes max). It is processed on this computer.
3. **Transcript review** — read the local transcription, correct recognition
   errors, save. Both the original and corrected transcripts are kept.
4. **Feedback** — approve the transcript to request local Gemma feedback:
   four 1–5 rubric scores with evidence quoted from your transcript, one
   strength, and two actionable suggestions. Labeled "AI practice assessment
   · not a validated measurement."
5. **History** — list attempts, retry the same topic, compare two finished
   attempts on the same topic side by side (score deltas plus the actual
   feedback), or delete a session and its audio.

## Limits

- Upload: 30 MB maximum (`MAX_UPLOAD_MB`).
- Duration: 180 seconds maximum (`MAX_DURATION_SECONDS`).
- Whisper CPU threads: 8 (`WHISPER_CPU_THREADS`).

## Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| `Install FFmpeg with the needed phone-audio decoders.` | `sudo dnf install ffmpeg`, then restart the backend. |
| `Prepare local small.en using scripts/prepare_model.py.` | Run `python scripts/prepare_model.py` with the venv active. |
| `GEMMA_MODEL` empty / tag not installed / not Gemma-family | Edit `.env`, run `ollama pull <exact tag>` (Gemma family only), restart. |
| Local Ollama request failed | Start Ollama (`systemctl --user start ollama` or `ollama serve`), check `OLLAMA_URL`, restart. |
| Cloud-backed models are not allowed | `GEMMA_MODEL` must name a locally pulled Gemma model; Ollama must not route to a remote host. |
| Audio could not be decoded | Export a valid m4a, mp3, wav, or webm from your phone; confirm your FFmpeg has the matching decoder. |
| Recording exceeds the 180-second limit | Trim the recording and upload again. |
| Too little speech was recognized | Record a longer response; short/silent audio is never scored as speaking ability. |
| Another backend is using this data directory | Only one uvicorn worker may run against a data directory (worker.lock). Stop the other instance. |
| Jobs stuck or `interrupted` on restart | A crash mid-processing leaves jobs marked `interrupted`; open the session and click Retry processing. |

Ports are bound to loopback only. If you run the frontend dev server, it also
binds `127.0.0.1:5173`.

## Testing

Backend tests (pytest):
```bash
source .venv/bin/activate
pytest
```

Smoke test for the complete workflow (mock mode, no Ollama needed):
```bash
source .venv/bin/activate
./scripts/smoke_test.sh
```

Frontend typecheck and build:
```bash
cd frontend
npm install
npx tsc -b
npm run build
```

## Tech stack

Frontend: React, TypeScript, Vite, Tailwind CSS. Backend: Python, FastAPI,
Pydantic. Database: SQLite. Audio conversion: FFmpeg. Transcription:
faster-whisper (`small.en`, CPU, int8). Feedback: local Gemma via Ollama.
No authentication, billing, cloud database, external analytics, hosted
inference, or agent framework.