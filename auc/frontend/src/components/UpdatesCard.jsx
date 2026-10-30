import React, { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw, ArrowUpCircle, Check, X, Circle, Loader2, AlertTriangle, MinusCircle,
  ExternalLink, Copy,
} from 'lucide-react';
import { getUpdateCheck, startUpdate, getUpdateStatus } from '../api';

// Settings → Updates: which version is running, whether GitHub has a newer
// one and what changed, and the update itself.
//
// The work happens on the server (backend/updater.py), in a job of its own.
// This card only starts it and reads its progress. While AUC restarts the
// progress cannot be read at all, which is expected, so a failed poll means
// "restarting", not "broken".

const POLL_MS = 2000;
// After this many polls in a row with no answer (five minutes), say so.
const PATIENCE_POLLS = 150;

const FINISHED = ['succeeded', 'failed', 'rolled_back', 'broken', 'interrupted'];

const FINISHED_TITLES = {
  succeeded: 'Update finished',
  failed: 'The update did not go ahead',
  rolled_back: 'The update was undone',
  broken: 'AUC needs attention',
  interrupted: 'The update was interrupted',
};

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function StepIcon({ status }) {
  switch (status) {
    case 'running':
      return <Loader2 size={16} className="icon-spin" />;
    case 'done':
      return <Check size={16} />;
    case 'failed':
      return <X size={16} />;
    case 'warning':
      return <AlertTriangle size={16} />;
    case 'skipped':
      return <MinusCircle size={16} />;
    default:
      return <Circle size={16} />;
  }
}

function ReleaseNotes({ notes }) {
  if (!notes?.length) return null;
  return (
    <div className="update-notes">
      {notes.map((release) => (
        <div key={release.version} className="update-notes__release">
          <div className="update-notes__version">
            Version {release.version}
            {release.published_at && (
              <span className="update-notes__date"> · {formatDate(release.published_at)}</span>
            )}
          </div>
          {release.changes?.length ? (
            <ul className="update-notes__list">
              {release.changes.map((change, i) => <li key={i}>{change}</li>)}
            </ul>
          ) : (
            <div className="settings-row__desc">No list of changes was published for this version.</div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function UpdatesCard({ onCheck }) {
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState('');

  const [status, setStatus] = useState(null);
  // True once this page has seen an update running, so it follows it to the end.
  const [following, setFollowing] = useState(false);
  const [missedPolls, setMissedPolls] = useState(0);

  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  const [copied, setCopied] = useState(false);

  const runCheck = useCallback(async (refresh) => {
    setChecking(true);
    setCheckError('');
    try {
      const result = await getUpdateCheck(refresh);
      setCheck(result);
      onCheck?.(result);
    } catch (err) {
      setCheckError(err.message || 'Could not check for updates.');
    }
    setChecking(false);
  }, [onCheck]);

  useEffect(() => {
    runCheck(false);
    getUpdateStatus()
      .then((s) => {
        setStatus(s);
        if (s.state === 'running') setFollowing(true);
      })
      .catch(() => {});
  }, [runCheck]);

  // Follow a running update until it finishes, through the restart.
  useEffect(() => {
    if (!following) return undefined;
    let cancelled = false;
    let timer;
    const poll = async () => {
      try {
        const s = await getUpdateStatus();
        if (cancelled) return;
        setStatus(s);
        setMissedPolls(0);
        if (s.state !== 'running') {
          // The new version serves new pages; load them.
          if (s.state === 'succeeded') timer = setTimeout(() => window.location.reload(), 4000);
          return;
        }
      } catch {
        if (cancelled) return;
        setMissedPolls((n) => n + 1);
      }
      timer = setTimeout(poll, POLL_MS);
    };
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [following]);

  const handleStart = async () => {
    setStarting(true);
    setStartError('');
    try {
      setStatus(await startUpdate(check.latest));
      setConfirming(false);
      setFollowing(true);
    } catch (err) {
      setStartError(err.message || 'The update could not be started.');
    }
    setStarting(false);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(
        `${status.message || ''}\n\n${status.log_tail || ''}`.trim(),
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch {
      // No clipboard over plain http from another computer; the details are
      // still there to select by hand.
    }
  };

  const running = status?.state === 'running';
  const finished = FINISHED.includes(status?.state);
  // A finished update is shown if this page followed it, or if it failed for
  // the version still on offer — so a failure seen by nobody is not lost.
  const showResult = finished && (
    following || (status.state !== 'succeeded' && check?.available && status.to_version === check.latest)
  );
  const showOffer = check?.available && !running && !(following && status?.state === 'succeeded');

  let summary;
  if (!check) {
    summary = checkError || 'Checking for updates…';
  } else if (check.error) {
    summary = check.error;
  } else if (check.installed === 'dev') {
    summary = `This is a development copy. The newest release is ${check.latest}.`;
  } else if (check.available) {
    summary = `Version ${check.latest} is available.`;
  } else {
    summary = `You're up to date. Checked ${formatDateTime(check.checked_at)}; AUC checks once a day.`;
    if (status?.state === 'succeeded' && status.to_version === check.installed) {
      summary += ` Updated from version ${status.from_version} on ${formatDate(status.finished_at)}.`;
    }
  }

  return (
    <div className="card settings-card">
      <div className="settings-row">
        <div>
          <div className="settings-row__label">
            AUC version {check?.installed || '…'}
          </div>
          <div className="settings-row__desc">{summary}</div>
          {check && checkError && (
            <div className="settings-row__desc settings-row__note settings-row__note--error">
              {checkError}
            </div>
          )}
        </div>
        <div className="settings-row__control">
          <button
            className="btn btn--secondary"
            onClick={() => runCheck(true)}
            disabled={checking || running}
          >
            <RefreshCw size={15} className={checking ? 'icon-spin' : ''} />
            {checking ? 'Checking…' : 'Check for updates'}
          </button>
        </div>
      </div>

      {(running || showResult) && status?.steps && (
        <div className="update-panel">
          <div className="update-panel__title">
            {running ? (
              <>
                <Loader2 size={18} className="icon-spin" />
                <span>Updating to version {status.to_version}</span>
              </>
            ) : (
              <span>{FINISHED_TITLES[status.state]}</span>
            )}
          </div>
          <ol className="update-steps">
            {status.steps.map((step) => (
              <li key={step.id} className={`update-steps__item update-steps__item--${step.status}`}>
                <StepIcon status={step.status} />
                <div>
                  <div>{step.label}</div>
                  {step.detail && <div className="update-steps__detail">{step.detail}</div>}
                </div>
              </li>
            ))}
          </ol>

          {running && missedPolls > 0 && missedPolls < PATIENCE_POLLS && (
            <div className="settings-row__desc">
              AUC is restarting. This page carries on by itself when it answers again.
            </div>
          )}
          {running && missedPolls >= PATIENCE_POLLS && (
            <div className="alert alert--warning">
              <AlertTriangle size={16} />
              <span>
                AUC has not answered for five minutes. Reload this page in a minute or two. If it
                still does not load, run systemctl --user restart auc on the AUC computer.
              </span>
            </div>
          )}

          {finished && status.message && (
            <div className={`alert ${status.state === 'succeeded' ? 'alert--success' : 'alert--danger'}`}>
              {status.state === 'succeeded' ? <Check size={16} /> : <AlertTriangle size={16} />}
              <span>
                {status.message}
                {status.state === 'succeeded' && following && ' Reloading the page…'}
              </span>
            </div>
          )}
          {status.warnings?.map((warning) => (
            <div key={warning} className="alert alert--warning">
              <AlertTriangle size={16} /> <span>{warning}</span>
            </div>
          ))}

          {finished && status.state !== 'succeeded' && status.log_tail && (
            <details className="update-details">
              <summary>Details</summary>
              <pre>{status.log_tail}</pre>
              {navigator.clipboard && (
                <button className="btn btn--secondary btn--sm" onClick={handleCopy}>
                  <Copy size={13} /> {copied ? 'Copied' : 'Copy details'}
                </button>
              )}
            </details>
          )}
        </div>
      )}

      {showOffer && (
        <div className="update-panel">
          <div className="update-panel__title">
            <ArrowUpCircle size={18} />
            <span>
              Version {check.latest} is available
              {check.published_at && (
                <span className="update-notes__date"> · published {formatDate(check.published_at)}</span>
              )}
            </span>
          </div>
          <div className="update-panel__subtitle">What&apos;s changed since version {check.installed}</div>
          <ReleaseNotes notes={check.notes} />
          {check.release_url && (
            <a className="update-panel__link" href={check.release_url} target="_blank" rel="noreferrer">
              Full release notes on GitHub <ExternalLink size={13} />
            </a>
          )}
          <div className="update-panel__actions">
            {check.can_update ? (
              <button
                className="btn btn--primary"
                onClick={() => { setStartError(''); setConfirming(true); }}
              >
                <ArrowUpCircle size={15} /> Update to {check.latest}
              </button>
            ) : (
              <div className="settings-row__desc">{check.reason}</div>
            )}
          </div>
        </div>
      )}

      {confirming && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="update-confirm-title">
          <div className="modal">
            <h2 id="update-confirm-title">Update AUC to version {check.latest}?</h2>
            <ul className="update-confirm__list">
              <li>
                The new version is downloaded from GitHub, checked, and set up beside the current
                one while AUC keeps running. This usually takes a few minutes.
              </li>
              <li>
                Then AUC pauses for about a minute: your database and photos are backed up, and
                the new version is started.
              </li>
              <li>
                Anyone using AUC during that minute needs to reload the page afterwards. A summary
                being written at that moment is lost, so let any summary finish first.
              </li>
              <li>
                If the new version does not start, AUC goes back to version {check.installed} and
                your database is put back as it was.
              </li>
            </ul>
            {startError && (
              <div className="alert alert--danger mt-md">
                <AlertTriangle size={16} /> <span>{startError}</span>
              </div>
            )}
            <div className="modal__actions">
              <button className="btn btn--secondary" onClick={() => setConfirming(false)} disabled={starting}>
                Cancel
              </button>
              <button className="btn btn--primary" onClick={handleStart} disabled={starting}>
                {starting ? 'Starting…' : 'Update now'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
