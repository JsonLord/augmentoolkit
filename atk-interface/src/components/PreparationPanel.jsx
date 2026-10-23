import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authenticatedFetch } from '../utils/operatorAuth';

async function request(path, options) {
  const response = await authenticatedFetch('/pilot/preparation' + path, options);
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.detail || 'Preparation unavailable');
  }
  return response.json();
}

export default function PreparationPanel({ sourcePath, mode = 'monitor' }) {
  const navigate = useNavigate();
  const [config, setConfig] = useState('');
  const [configs, setConfigs] = useState([]);
  const [d0, setD0] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try { const rows = await request(''); if (active) { setJobs(rows); setError(''); } }
      catch (e) { if (active) setError(e.message); }
    };
    refresh();
    const timer = setInterval(refresh, 2000);
    if (mode === 'submit') {
      authenticatedFetch('/configs/structure/').then(async r => {
        if (!r.ok) return;
        const files = [];
        const walk = nodes => nodes.forEach(n => n.is_dir ? walk(n.children || []) : /\.ya?ml$/i.test(n.path) && files.push(n.path));
        walk(await r.json()); if (active) setConfigs(files);
      }).catch(() => { if (active) setError('Private configurations unavailable'); });
    }
    return () => { active = false; clearInterval(timer); };
  }, [mode]);
  const download = async (id, name) => {
    try {
      const r = await authenticatedFetch(`/pilot/preparation/${id}/download/${name}`);
      if (!r.ok) throw new Error('Artifact download failed');
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement('a'); a.href = url; a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError(e.message); }
  };
  return <section data-action-button="true" className="p-4 bg-gray-800 rounded my-2" aria-label="Dataset preparation">
    <h2>Prepare Dataset — deterministic text chunks</h2>
    <p>Preparation JSONL contains source chunks, not generated Q&amp;A. Local D0 TXT only; no model calls.</p>
    {mode === 'submit' && <>
      <p>Selected input: {sourcePath || 'Select one TXT file in the input tree.'}</p>
      <label>Private preparation config <select className="text-black" aria-label="Private preparation config" value={config} onChange={e => setConfig(e.target.value)}>
        <option value="">Choose private YAML</option>{configs.map(p => <option key={p}>{p}</option>)}
      </select></label>
      <label className="block"><input type="checkbox" checked={d0} onChange={e => setD0(e.target.checked)} /> I confirm this input is public/synthetic D0</label>
      <button disabled={!sourcePath || !config || !d0 || busy} onClick={async () => {
        setBusy(true); setError('');
        try { await request('', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_path: sourcePath, config_path: config, sensitivity: 'D0'})}); navigate('/monitor'); }
        catch (e) { setError(e.message); } finally { setBusy(false); }
      }}>Prepare Dataset</button>
    </>}
    {error && <p role="alert">Not available: {error}</p>}
    {jobs.map(job => <article key={job.job_id} className="border p-2 my-2" aria-label={`Preparation ${job.job_id}`}>
      <p>Job {job.job_id}: <strong>{job.status}</strong> — {job.phase}; {job.record_count} chunks</p>
      {job.error && <p>{job.error}</p>}
      {job.status === 'COMPLETED' && <>
        <button onClick={async () => { try { setPreview((await request('/' + job.job_id + '/preview')).records); } catch (e) { setError(e.message); } }}>Preview JSONL</button>
        {' '}<button onClick={() => download(job.job_id, 'records.jsonl')}>Download JSONL</button>
        {' '}<button onClick={() => download(job.job_id, 'manifest.json')}>Download manifest</button>
      </>}
    </article>)}
    {preview && <pre aria-label="JSONL preview" className="whitespace-pre-wrap">{preview.map(r => JSON.stringify(r)).join('\n')}</pre>}
  </section>;
}
