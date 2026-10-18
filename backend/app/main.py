import fcntl
import json
import os
import queue
import shutil
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import SOURCE, Settings
from .inference import PracticeError, analyze, check_gemma, convert_audio, model_ready, transcribe
from .rubric import RUBRIC, word_count
from .storage import Store

TOPICS = [
    'What is one small habit that has made your day better?',
    'Describe a time you changed your mind. What helped you see things differently?',
    'What is a skill everyone should learn, and why?',
    'Tell someone about a place that matters to you.',
    'Should cities make more room for walking? Explain your view.',
    'Describe something you learned from a mistake.',
    'What makes someone a good teammate? Give an example.',
    'Recommend a book, film, or activity and explain who would enjoy it.',
    'If you could improve one thing in your neighborhood, what would it be?',
    'Explain a useful idea from your work to someone unfamiliar with it.',
    'What is one tradition you would like to keep alive?',
    'Describe a difficult decision and how you approached it.',
    'What does a good day off look like for you?',
    'Is it better to plan carefully or learn by doing? Give an example.',
]
BUSY = {'queued', 'converting', 'transcribing', 'analyzing'}
MEDIA = {'.m4a': 'audio/mp4', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.webm': 'audio/webm'}


class TranscriptUpdate(BaseModel):
    transcript: str = Field(max_length=12000)


class Worker:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.jobs = queue.Queue()
        self.lock = threading.RLock()
        self.thread = threading.Thread(target=self.run, daemon=True, name='practice-worker')
        self.thread.start()

    def run(self):
        while (sid := self.jobs.get()) is not None:
            session = self.store.get(sid)
            if not session:
                continue
            try:
                directory = self.settings.data_dir / 'audio' / sid
                if session['job_kind'] == 'transcribe':
                    self.store.update(sid, status='converting', error=None)
                    duration = convert_audio(directory / 'original', directory / 'normalized.wav', self.settings)
                    self.store.update(sid, status='transcribing', duration=duration)
                    text = transcribe(directory / 'normalized.wav', self.settings)
                    self.store.update(sid, original_transcript=text, corrected_transcript=text,
                                      status='review', error=None if word_count(text) >= 20 else
                                      'Too little speech was recognized. Check playback and the transcript; record a longer response if needed. This is not a speaking score.')
                else:
                    self.store.update(sid, status='analyzing', error=None)
                    result = analyze(session, self.settings)
                    self.store.update(sid, status='complete', feedback=result)
            except PracticeError as exc:
                self.store.update(sid, status='error', error=str(exc))
            except Exception:
                # Never log model prompts, transcripts, audio, or exception payloads.
                self.store.update(sid, status='error', error='Local processing failed. Check dependency setup and available memory, then retry. No assessment was saved.')


def create_app(settings=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_file = (settings.data_dir / 'worker.lock').open('w')
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError('Another backend is using this data directory. Run one Uvicorn worker.') from None
        store = Store(settings.data_dir)
        store.interrupt()
        # Remove abandoned uploads from a crash between file write and database insert.
        audio_root = settings.data_dir / 'audio'
        audio_root.mkdir(exist_ok=True)
        known = {s['id'] for s in store.all()}
        for directory in audio_root.iterdir():
            if directory.is_dir() and directory.name not in known:
                shutil.rmtree(directory)
        app.state.store = store
        app.state.worker = Worker(store, settings)
        yield
        app.state.worker.jobs.put(None)
        # Process exit stops the daemon; persisted in-flight work is interrupted on restart.
        app.state.worker.thread.join(timeout=2)
        lock_file.close()

    app = FastAPI(title='Speaking Room — local practice', lifespan=lifespan)

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        from urllib.parse import urlparse
        allowed = {'127.0.0.1', 'localhost', '::1', 'testserver'}
        if request.url.hostname not in allowed:
            return JSONResponse({'detail': 'Only local access is allowed.'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin not in {f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (5173, 8000)}:
            return JSONResponse({'detail': 'Requests must come from the local application.'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def get_session(sid):
        result = app.state.store.get(sid)
        if not result:
            raise HTTPException(404, 'Session not found.')
        text = result['corrected_transcript'] or ''
        result['word_count'] = word_count(text)
        result['words_per_minute'] = round(word_count(text) * 60 / result['duration']) if result['duration'] else None
        return result

    def editable(session):
        if session['status'] in BUSY:
            raise HTTPException(409, 'Wait for processing to finish before changing or deleting this attempt.')

    @app.get('/api/health')
    def health():
        ollama, gemma, issue = False, False, None
        try:
            with httpx.Client(base_url=settings.ollama_url, timeout=3, trust_env=False) as client:
                r = client.get('/api/tags')
                r.raise_for_status()
                ollama = True
                check_gemma(client, settings)
                gemma = True
        except PracticeError as exc:
            issue = str(exc)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            issue = 'Start local Ollama, configure GEMMA_MODEL, and restart. See README setup.'
        ffmpeg = bool(shutil.which('ffmpeg'))
        whisper = model_ready(settings)
        return {'ready': ffmpeg and (settings.mock or (whisper and gemma)), 'mock': settings.mock,
                'ffmpeg': ffmpeg, 'transcription_model': whisper, 'ollama': ollama,
                'gemma_model': gemma, 'configured_model': settings.gemma_model or None,
                'issues': ([] if ffmpeg else ['Install FFmpeg with the needed phone-audio decoders.']) +
                          ([] if whisper else ['Prepare local small.en using scripts/prepare_model.py.']) + ([issue] if issue else []),
                'max_upload_mb': settings.max_upload_mb, 'max_duration_seconds': settings.max_duration}

    @app.get('/api/topics/today')
    def today(local_date: date | None = None):
        day = local_date or date.today()
        return {'date': day.isoformat(), 'topic': TOPICS[day.toordinal() % len(TOPICS)],
                'suggested_seconds': [60, 90]}

    @app.get('/api/rubric')
    def rubric():
        return {'version': '1.0', 'dimensions': RUBRIC}

    @app.post('/api/sessions', status_code=201)
    def upload(topic: str = Form(min_length=1, max_length=500), file: UploadFile = File()):
        if not topic.strip():
            raise HTTPException(422, 'Enter a topic.')
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in MEDIA:
            raise HTTPException(415, 'Choose an m4a, mp3, wav, or webm recording.')
        sid = uuid4().hex
        directory = settings.data_dir / 'audio' / sid
        directory.mkdir(parents=True, mode=0o700)
        try:
            size = 0
            with (directory / 'original').open('wb') as target:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, f'Upload exceeds {settings.max_upload_mb} MB. Export a smaller file.')
                    target.write(chunk)
            if size == 0:
                raise HTTPException(422, 'The file is empty. Choose a recording with audio.')
            with app.state.store.connection() as db:
                db.execute('INSERT INTO sessions(id,topic,created_at,status,mock,media_type) VALUES(?,?,?,?,?,?)',
                           (sid, topic.strip(), datetime.now(timezone.utc).isoformat(), 'queued', int(settings.mock), MEDIA[suffix]))
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            file.file.close()
        app.state.worker.jobs.put(sid)
        return get_session(sid)

    @app.get('/api/sessions')
    def sessions():
        return app.state.store.all()

    @app.get('/api/sessions/{sid}')
    def session(sid: str):
        return get_session(sid)

    @app.get('/api/sessions/{sid}/audio')
    def audio(sid: str):
        session = get_session(sid)
        return FileResponse(settings.data_dir / 'audio' / session['id'] / 'original', media_type=session['media_type'])

    @app.patch('/api/sessions/{sid}/transcript')
    def correct(sid: str, body: TranscriptUpdate):
        with app.state.worker.lock:
            current = get_session(sid)
            editable(current)
            if current['original_transcript'] is None:
                raise HTTPException(409, 'Complete transcription before editing.')
            app.state.store.update(sid, corrected_transcript=body.transcript.strip(), feedback=None,
                                   status='review', error=None)
            return get_session(sid)

    def enqueue(sid, kind):
        current = get_session(sid)
        editable(current)
        if current['mock'] != settings.mock:
            raise HTTPException(409, 'This attempt belongs to a different mock/real mode. Start a new attempt in the current mode.')
        if kind == 'analyze' and word_count(current['corrected_transcript'] or '') < 20:
            raise HTTPException(422, 'At least 20 recognized or corrected words are needed. Record a longer response; no scores will be forced.')
        app.state.store.update(sid, job_kind=kind, status='queued', error=None, feedback=None)
        app.state.worker.jobs.put(sid)
        return get_session(sid)

    @app.post('/api/sessions/{sid}/analyze', status_code=202)
    def request_analysis(sid: str):
        with app.state.worker.lock:
            current = get_session(sid)
            if current['original_transcript'] is None:
                raise HTTPException(409, 'Complete transcription and review it first.')
            return enqueue(sid, 'analyze')

    @app.post('/api/sessions/{sid}/retry', status_code=202)
    def retry(sid: str):
        with app.state.worker.lock:
            current = get_session(sid)
            if current['status'] not in {'error', 'interrupted'}:
                raise HTTPException(409, 'Only failed or interrupted jobs can be retried.')
            return enqueue(sid, current['job_kind'])

    @app.delete('/api/sessions/{sid}', status_code=204)
    def delete(sid: str):
        with app.state.worker.lock:
            current = get_session(sid)
            editable(current)
            directory = settings.data_dir / 'audio' / current['id']
            if directory.exists():
                shutil.rmtree(directory)
            with app.state.store.connection() as db:
                db.execute('DELETE FROM sessions WHERE id=?', (sid,))

    dist = SOURCE / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app


app = create_app()
