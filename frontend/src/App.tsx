import { useEffect, useRef, useState } from 'react'

type Dimension = 'relevance' | 'organization' | 'support' | 'concision'
type Feedback = { rubric_version: string; scores: Record<Dimension, {score: number; explanation: string; evidence: string}>; strength: string; improvements: {observation: string; evidence: string; suggested_action: string}[]; assessment_limitations: string }
type Session = {id: string; topic: string; created_at: string; status: string; error: string | null; original_transcript: string | null; corrected_transcript: string | null; feedback: Feedback | null; duration: number | null; words_per_minute?: number; mock: boolean}
type Health = {ready: boolean; mock: boolean; ffmpeg: boolean; transcription_model: boolean; ollama: boolean; gemma_model: boolean; configured_model: string | null; issues: string[]; max_upload_mb: number; max_duration_seconds: number}
const names: Record<Dimension, string> = {relevance: 'Relevance', organization: 'Organization', support: 'Support & examples', concision: 'Concision'}
const busyStates = ['queued', 'converting', 'transcribing', 'analyzing']
const stages: Record<string, string> = {queued: 'Waiting for the local worker', converting: 'Checking & preparing audio', transcribing: 'Transcribing on this computer', analyzing: 'Gemma is preparing feedback', review: 'Ready for transcript review', complete: 'Feedback ready', error: 'Processing needs attention', interrupted: 'Processing interrupted'}
const dateLabel = (value: string) => new Date(value).toLocaleDateString(undefined, {month: 'short', day: 'numeric'})
const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` }
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status}). Check your input and local backend.`)
  }
  return response.status === 204 ? undefined as T : response.json()
}

function FeedbackCard({session}: {session: Session}) {
  const f = session.feedback!
  return <div className="feedback-content">
    <div className="assessment-note">{session.mock ? 'MOCK demonstration · no real assessment' : 'AI practice assessment · not a validated measurement'} · Rubric {f.rubric_version}</div>
    <div className="score-grid">{(Object.keys(names) as Dimension[]).map(key => <article className="score-card" key={key}>
      <div className="flex justify-between items-center gap-3"><h3>{names[key]}</h3><span className="score">{f.scores[key].score}<small> / 5</small></span></div>
      <div className="score-track" aria-hidden="true">{[1,2,3,4,5].map(n => <i className={n <= f.scores[key].score ? 'filled' : ''} key={n}/>)}</div>
      <p>{f.scores[key].explanation}</p><blockquote>“{f.scores[key].evidence}”</blockquote>
    </article>)}</div>
    <article className="strength"><span className="eyebrow">Keep doing this</span><h3>One strength to build on</h3><p>{f.strength}</p></article>
    <h3 className="mt-7 mb-4">Two things to try next time</h3>
    {f.improvements.map((item, i) => <article className="suggestion" key={i}><span className="number">0{i+1}</span><div><h4>{item.suggested_action}</h4><p>{item.observation}</p><blockquote>“{item.evidence}”</blockquote></div></article>)}
    <p className="fine-print">{f.assessment_limitations}</p>
    <p className="fine-print">These scores describe the approved text, not confidence, personality, pronunciation, or stuttering. Transcript-based filler counts would not be complete.</p>
  </div>
}

export default function App() {
  const [page, setPage] = useState<'home'|'practice'|'session'|'history'|'compare'>('home')
  const [health, setHealth] = useState<Health | null>(null)
  const [daily, setDaily] = useState('')
  const [topic, setTopic] = useState('')
  const [custom, setCustom] = useState('')
  const [sessions, setSessions] = useState<Session[]>([])
  const [current, setCurrent] = useState<Session | null>(null)
  const [transcript, setTranscript] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [rubric, setRubric] = useState<Record<Dimension, string[]> | null>(null)
  const [dragging, setDragging] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const heading = useRef<HTMLElement>(null)
  const activeId = useRef<string | null>(null)

  const refresh = async () => setSessions(await api<Session[]>('/sessions'))
  useEffect(() => {
    Promise.all([
      api<Health>('/health').then(setHealth), refresh(),
      api<{topic: string}>(`/topics/today?local_date=${localDate()}`).then(r => setDaily(r.topic)),
      api<{dimensions: Record<Dimension, string[]>}>('/rubric').then(r => setRubric(r.dimensions)),
    ]).catch(e => setError(e.message))
  }, [])
  useEffect(() => { heading.current?.focus(); setDeleting(false) }, [page, current?.id])
  useEffect(() => {
    if (!current || !busyStates.includes(current.status)) return
    const id = current.id
    let stopped = false
    const tick = async () => {
      try {
        const result = await api<Session>(`/sessions/${id}`)
        if (stopped || activeId.current !== id) return
        setCurrent(result)
        if (!busyStates.includes(result.status)) { setTranscript(result.corrected_transcript || ''); await refresh() }
      } catch (e) { if (!stopped) setError((e as Error).message) }
    }
    const timer = setInterval(tick, 1600)
    return () => { stopped = true; clearInterval(timer) }
  }, [current?.id, current?.status])

  async function act(fn: () => Promise<void>) {
    setPending(true); setError('')
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setPending(false) }
  }
  function start(value: string) { setTopic(value); setFile(null); setError(''); setPage('practice') }
  function show(result: Session) { activeId.current = result.id; setCurrent(result); setTranscript(result.corrected_transcript || ''); setPage('session') }
  async function open(id: string) { await act(async () => show(await api<Session>(`/sessions/${id}`))) }
  function selectFile(f?: File) {
    if (!f) return
    setError('')
    if (!/\.(m4a|mp3|wav|webm)$/i.test(f.name)) { setError('Choose an m4a, mp3, wav, or webm recording.'); return }
    if (f.size > (health?.max_upload_mb || 30) * 1024 * 1024) { setError(`Choose a file under ${health?.max_upload_mb || 30} MB.`); return }
    setFile(f)
  }
  async function upload() { if (!file) return; await act(async () => {
    const body = new FormData(); body.append('topic', topic); body.append('file', file)
    show(await api<Session>('/sessions', {method: 'POST', body})); await refresh()
  }) }
  async function save(analyze = false) { if (!current) return; await act(async () => {
    const saved = await api<Session>(`/sessions/${current.id}/transcript`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({transcript})})
    setCurrent(saved)
    if (analyze) setCurrent(await api<Session>(`/sessions/${current.id}/analyze`, {method:'POST'}))
    await refresh()
  }) }
  const busy = !!current && busyStates.includes(current.status)
  const comparable = sessions.filter(s => selected.includes(s.id))

  function historyList(items: Session[], selectable = false) {
    return items.length ? <div className="attempt-list">{items.map(s => <div className="attempt" key={s.id}>
      {selectable && <input type="checkbox" aria-label={`Compare attempt ${dateLabel(s.created_at)} ${s.id.slice(0,6)}`} checked={selected.includes(s.id)} disabled={!s.feedback || (!selected.includes(s.id) && (selected.length === 2 || (comparable[0] && (comparable[0].topic !== s.topic || comparable[0].mock !== s.mock))))} onChange={() => setSelected(selected.includes(s.id) ? selected.filter(id => id !== s.id) : [...selected,s.id])}/>}
      <button className="attempt-link" onClick={() => open(s.id)} disabled={pending}><span className="attempt-date">{dateLabel(s.created_at)}</span><span><strong>{s.topic}</strong><span className="attempt-meta">{stages[s.status] || s.status}{s.mock ? ' · MOCK' : ''}{s.duration ? ` · ${Math.round(s.duration)} sec` : ''}</span></span><span aria-hidden="true">↗</span></button>
    </div>)}</div> : <div className="empty"><span aria-hidden="true">◌</span><h3>Your practice starts here.</h3><p>Your recordings and feedback will appear here after your first attempt.</p></div>
  }

  return <div className="app-shell">
    <header className="site-header"><button className="brand" onClick={() => {setPage('home'); setError('')}} aria-label="Speaking Room home"><span className="brand-mark" aria-hidden="true"><i/><i/><i/><i/><i/></span>speaking room<span className="brand-period">.</span></button><nav aria-label="Main navigation"><button className={page !== 'history' && page !== 'compare' ? 'nav-active' : ''} onClick={() => setPage('home')}>Practice</button><button className={page === 'history' || page === 'compare' ? 'nav-active' : ''} onClick={() => {setPage('history'); act(refresh)}}>History</button></nav><span className="local-badge"><i/> Local & private</span></header>
    {health?.mock && <div className="mock-banner" role="status">MOCK MODE — sample transcription and feedback. Audio conversion is real; no AI model is evaluating your recording.</div>}
    <main ref={heading} tabIndex={-1}>
      {error && <div className="error" role="alert">{error}<button aria-label="Dismiss error" onClick={() => setError('')}>×</button></div>}
      {health && !health.ready && <details className="setup"><summary>Local setup needs attention</summary><ul>{health.issues.map(i => <li key={i}>{i}</li>)}</ul><button className="text-button" onClick={() => act(async () => setHealth(await api<Health>('/health')))}>Recheck dependencies</button><p>See README.md for Fedora and model setup. There is no cloud fallback.</p></details>}
      {page === 'home' && <>
        <div className="intro"><span className="eyebrow">A little practice. A clearer voice.</span><h1>Improve your<br/><em>speaking</em>skills.</h1><p>One topic. A minute of speaking.<br className="hidden sm:block"/>Find a clearer way to say what you mean.</p></div>
        <div className="home-grid"><section className="topic-card"><div className="flex justify-between items-center gap-3"><span className="eyebrow">Today's conversation</span><span className="pill">60–90 seconds</span></div><h2>{daily || 'Loading your topic…'}</h2><p>Take a moment to think. Make your point, share an example, and bring it back home.</p><button className="primary" disabled={!daily} onClick={() => start(daily)}>Start practice <span aria-hidden="true">↗</span></button><div className="topic-footer"><span aria-hidden="true">✳</span> A fresh prompt each day. Your own pace, always.</div></section>
          <aside className="how-card"><span className="eyebrow">A simple rhythm</span>{[['Record your thoughts','Use your phone to record a short response.'],['Bring your recording','Transfer the file to this laptop and select it here.'],['Reflect & try again','Review the transcript, get feedback, and give it another go.']].map(([title, text], i) => <div className="how-step" key={title}><span>0{i+1}</span><div><h3>{title}</h3><p>{text}</p></div></div>)}<div className="privacy-note">⌂ &nbsp; Your voice stays on this computer.</div></aside></div>
        <form className="custom-topic" onSubmit={e => {e.preventDefault(); if (custom.trim()) start(custom.trim())}}><label htmlFor="custom"><strong>Something else on your mind?</strong><span>Practice a topic of your own.</span></label><div className="flex gap-2"><input id="custom" maxLength={500} placeholder="Enter a custom topic" value={custom} onChange={e => setCustom(e.target.value)}/><button className="secondary" disabled={!custom.trim()}>Use topic <span aria-hidden="true">→</span></button></div></form>
        <section className="recent"><div className="section-heading"><h2>Recent practice</h2><button className="text-button" onClick={() => {setPage('history'); act(refresh)}}>View history →</button></div>{historyList(sessions.slice(0,3))}</section>
      </>}
      {page === 'practice' && <div className="narrow"><button className="back" onClick={() => setPage('home')}>← Back to home</button><span className="eyebrow">01 / Upload</span><h1>Your next attempt.</h1><div className="topic-strip"><span className="eyebrow">Your topic · aim for 60–90 seconds</span><h2>{topic}</h2></div><p className="lead">Record on your phone, transfer the file to your laptop, then select it below. Everything is processed on this computer.</p><label className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={e => {e.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={e => {e.preventDefault(); setDragging(false); selectFile(e.dataTransfer.files[0])}}><span className="upload-icon" aria-hidden="true">↑</span><strong>{file ? file.name : 'Drop your recording here'}</strong><span>{file ? `${(file.size/1024/1024).toFixed(1)} MB · click to choose another file` : 'or click to choose a file'}</span><input aria-label="Select audio recording" type="file" accept=".m4a,.mp3,.wav,.webm" onChange={e => selectFile(e.target.files?.[0])}/><small>M4A, MP3, WAV, WEBM · up to {health?.max_upload_mb || 30} MB · {health?.max_duration_seconds || 180} seconds maximum</small></label><button className="primary w-full" disabled={!file || pending} onClick={upload}>{pending ? 'Uploading to local backend…' : 'Upload & transcribe'} <span aria-hidden="true">→</span></button><p className="fine-print text-center">Decoder support depends on your installed FFmpeg. Your original audio is saved for playback.</p></div>}
      {page === 'session' && current && <div className="session-view"><button className="back" onClick={() => {setPage('history'); act(refresh)}}>← All attempts</button><div className="section-heading"><div><span className="eyebrow">{current.feedback ? '03 / Reflect & repeat' : '02 / Listen & review'} · {dateLabel(current.created_at)}</span><h1>{current.feedback ? 'A little perspective.' : 'Your words, in focus.'}</h1></div>{current.mock && <span className="pill mock-pill">MOCK ATTEMPT</span>}</div><div className="topic-strip"><span className="eyebrow">Your topic</span><h2>{current.topic}</h2></div>
        <div className="recording"><div><span className="eyebrow">Original recording</span><p>{current.duration ? `${current.duration.toFixed(1)} seconds` : 'Duration pending'}{current.words_per_minute ? ` · ≈ ${current.words_per_minute} words/min` : ''}</p></div><audio key={current.id} controls preload="metadata" src={`/api/sessions/${current.id}/audio`}/></div><p className="fine-print">Speaking rate uses corrected word count and total recording duration, including pauses. It is approximate and is not a rubric score.</p>
        {busy && <div className="processing" role="status"><span className="spinner"/><div><h3>{stages[current.status]}…</h3><p>CPU inference can take a few minutes. You can leave this page and return from History.</p></div></div>}
        {current.error && <div className="error" role="alert"><div><strong>{stages[current.status]}</strong><p>{current.error}</p></div></div>}
        {['error','interrupted'].includes(current.status) && <button className="secondary mb-6" disabled={pending} onClick={() => act(async () => setCurrent(await api<Session>(`/sessions/${current.id}/retry`, {method:'POST'})))}>Retry processing</button>}
        {current.original_transcript !== null && !busy && <section className="transcript-section"><div className="section-heading"><div><h2>Review your transcript</h2><p>Correct recognition errors before requesting feedback.</p></div></div><label className="sr-only" htmlFor="transcript">Corrected transcript</label><textarea id="transcript" value={transcript} maxLength={12000} onChange={e => setTranscript(e.target.value)} rows={9}/><details className="original"><summary>View original transcription</summary><p>{current.original_transcript || 'No speech was recognized.'}</p></details><div className="actions"><button className="primary" disabled={pending || !transcript.trim()} onClick={() => save(true)}>{pending ? 'Saving…' : 'Approve transcript & get feedback'} <span aria-hidden="true">→</span></button><button className="secondary" disabled={pending || transcript === current.corrected_transcript} onClick={() => save()}>Save corrections</button></div><p className="fine-print">Saving corrections replaces previous feedback for this attempt. Recognition errors are never scored as speaking ability.</p></section>}
        {current.feedback && <section className="feedback"><div className="section-heading"><h2>Your practice feedback</h2></div><FeedbackCard session={current}/></section>}
        {rubric && <details className="rubric"><summary>How the rubric works · four dimensions, five anchors</summary><p>Each dimension is assessed independently. There is no overall percentage.</p>{(Object.keys(names) as Dimension[]).map(key => <div key={key}><h3>{names[key]}</h3><ol>{rubric[key].map((anchor,i) => <li key={anchor}><b>{i+1}</b> {anchor}</li>)}</ol></div>)}</details>}
        <div className="session-bottom"><button className="primary" onClick={() => start(current.topic)}>Try this topic again ↗</button>{!busy && (!deleting ? <button className="danger-link" onClick={() => setDeleting(true)}>Delete attempt</button> : <div className="delete-confirm"><span>Permanently delete this attempt and its audio?</span><button className="danger-link" disabled={pending} onClick={() => act(async () => {await api(`/sessions/${current.id}`, {method:'DELETE'}); activeId.current = null; setCurrent(null); await refresh(); setPage('history')})}>Yes, delete</button><button className="text-button" onClick={() => setDeleting(false)}>Cancel</button></div>)}</div>
      </div>}
      {page === 'history' && <><span className="eyebrow">A record of showing up</span><h1>Your practice, over time.</h1><p className="lead">Revisit a response, or select two finished attempts on the same topic to compare.</p><div className="history-toolbar"><span>{sessions.length} saved {sessions.length === 1 ? 'attempt' : 'attempts'}</span><button className="secondary" disabled={selected.length !== 2} onClick={() => setPage('compare')}>Compare selected ({selected.length}/2) →</button></div>{historyList(sessions,true)}<p className="fine-print mt-5">Mock and real attempts are kept separate in comparisons. A score increase does not prove real improvement.</p></>}
      {page === 'compare' && <><button className="back" onClick={() => setPage('history')}>← Back to history</button><span className="eyebrow">Reflect on the differences</span><h1>Two takes. Your perspective.</h1><h2 className="mb-6">{comparable[0]?.topic}</h2><p className="assessment-note">Score changes are model judgments, not proof of real improvement. Look at the words and the feedback, too.</p>{comparable.length === 2 && <><div className="delta-grid">{(Object.keys(names) as Dimension[]).map(k => {const ordered = [...comparable].sort((a,b) => a.created_at.localeCompare(b.created_at)); const delta = ordered[1].feedback!.scores[k].score - ordered[0].feedback!.scores[k].score; return <div key={k}><span>{names[k]}</span><strong>{delta > 0 ? '+' : ''}{delta}</strong><small>newer minus older</small></div>})}</div><div className="comparison">{[...comparable].sort((a,b) => a.created_at.localeCompare(b.created_at)).map((s,i) => <section key={s.id}><span className="eyebrow">{i ? 'Newer' : 'Older'} attempt · {new Date(s.created_at).toLocaleString()}</span><p>{s.duration?.toFixed(1)} seconds</p><audio controls preload="none" src={`/api/sessions/${s.id}/audio`}/><details className="original"><summary>Approved transcript</summary><p>{s.corrected_transcript}</p></details><FeedbackCard session={s}/></section>)}</div></>}</>}
    </main><footer><span>speaking room.</span><p>Made for practice, not perfection.</p><span>On your laptop. In your own time.</span></footer>
  </div>
}
