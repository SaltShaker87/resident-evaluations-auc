import { useState } from 'react';
import { getLog } from '../backend.js';
import { copyToClipboard } from '../utils.js';

export default function ErrorScreen({ outcome, onTryAgain }) {
  const [copied, setCopied] = useState(false);
  const err = outcome.error;

  const handleCopy = async () => {
    const log = await getLog();
    const text = [err?.message, err?.hint, log].filter(Boolean).join('\n\n');
    const ok = await copyToClipboard(text);
    setCopied(ok);
  };

  return (
    <div className="screen-content">
      <h1>{err?.message || 'Something went wrong'}</h1>
      {err?.hint && <p className="lead">{err.hint}</p>}
      <div className="screen-actions screen-actions--split">
        <button type="button" className="btn btn--secondary" onClick={handleCopy}>
          {copied ? 'Copied' : 'Copy details'}
        </button>
        <button type="button" className="btn btn--primary" onClick={onTryAgain}>
          Try again
        </button>
      </div>
    </div>
  );
}
