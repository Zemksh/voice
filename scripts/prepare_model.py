"""Explicit network download. Never invoked by the application."""
import os
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import snapshot_download

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
destination = Path(os.getenv('WHISPER_MODEL_DIR', '~/.local/share/voice-practice/models/small.en')).expanduser()
snapshot_download(repo_id='Systran/faster-whisper-small.en', local_dir=destination)
print(f'small.en downloaded to {destination}. Runtime uses local files only.')
