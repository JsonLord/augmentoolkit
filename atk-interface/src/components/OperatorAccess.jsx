import { useState } from 'react';
import { authenticatedFetch, setOperatorToken } from '../utils/operatorAuth';

export default function OperatorAccess({ onChange }) {
  const [entry, setEntry] = useState('');
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState('');
  const connect = async (event) => {
    event.preventDefault();
    setOperatorToken(entry);
    setEntry('');
    try {
      const response = await authenticatedFetch('/pipelines/available');
      if (!response.ok) throw new Error('Operator access unavailable');
      setConnected(true);
      setMessage('');
      onChange();
    } catch {
      setOperatorToken('');
      setMessage('Access denied or service unavailable');
    }
  };
  const lock = () => { setOperatorToken(''); setConnected(false); onChange(); };
  return <form onSubmit={connect} className="p-2 bg-gray-800 flex gap-2 items-center">
    {connected ? <button type="button" onClick={lock}>Lock operator access</button> : <>
      <label htmlFor="operator-token">Operator token</label>
      <input id="operator-token" type="password" autoComplete="off" value={entry}
        onChange={(event) => setEntry(event.target.value)} className="text-black" required />
      <button type="submit">Unlock operator access</button>
    </>}
    <span role="status">{message}</span>
  </form>;
}
