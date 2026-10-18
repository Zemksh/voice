import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

SOURCE = Path(__file__).resolve().parents[2]
load_dotenv(SOURCE / '.env')


@dataclass
class Settings:
    data_dir: Path
    whisper_dir: Path
    gemma_model: str = ''
    ollama_url: str = 'http://127.0.0.1:11434'
    max_upload_mb: int = 30
    max_duration: int = 180
    cpu_threads: int = 8
    mock: bool = False

    def __post_init__(self):
        self.data_dir = self.data_dir.expanduser().resolve()
        self.whisper_dir = self.whisper_dir.expanduser().resolve()
        if self.data_dir.is_relative_to(SOURCE):
            raise ValueError('VOICE_DATA_DIR must be outside the source directory.')
        url = urlparse(self.ollama_url)
        if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'}
                or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment):
            raise ValueError('OLLAMA_URL must be a loopback HTTP URL, e.g. http://127.0.0.1:11434.')
        self.ollama_url = self.ollama_url.rstrip('/')
        if min(self.max_upload_mb, self.max_duration, self.cpu_threads) <= 0:
            raise ValueError('Upload, duration, and thread limits must be positive.')

    @classmethod
    def from_env(cls):
        data = Path(os.getenv('VOICE_DATA_DIR', '~/.local/share/voice-practice'))
        return cls(data, Path(os.getenv('WHISPER_MODEL_DIR', str(data / 'models/small.en'))),
                   os.getenv('GEMMA_MODEL', '').strip(), os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434'),
                   int(os.getenv('MAX_UPLOAD_MB', '30')), int(os.getenv('MAX_DURATION_SECONDS', '180')),
                   int(os.getenv('WHISPER_CPU_THREADS', '8')), os.getenv('MOCK_MODE', 'false').lower() == 'true')
