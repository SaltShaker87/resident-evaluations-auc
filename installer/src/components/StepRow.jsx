import {
  AlertTriangle,
  Check,
  Circle,
  Loader2,
  Minus,
  X,
} from 'lucide-react';

function StatusIcon({ status }) {
  switch (status) {
    case 'done':
      return <Check className="step-icon step-icon--done" size={18} aria-hidden />;
    case 'running':
      return <Loader2 className="step-icon step-icon--running" size={18} aria-hidden />;
    case 'warning':
      return <AlertTriangle className="step-icon step-icon--warn" size={18} aria-hidden />;
    case 'failed':
      return <X className="step-icon step-icon--fail" size={18} aria-hidden />;
    case 'skipped':
      return <Minus className="step-icon step-icon--skip" size={18} aria-hidden />;
    default:
      return <Circle className="step-icon step-icon--pending" size={18} aria-hidden />;
  }
}

export default function StepRow({ step }) {
  const { label, status, detail, progress } = step;
  return (
    <li className={`step-row step-row--${status}`}>
      <StatusIcon status={status} />
      <div className="step-row__body">
        <div className="step-row__label">{label}</div>
        {detail && <div className="step-row__detail">{detail}</div>}
        {progress != null && status === 'running' && (
          <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div className="progress-bar__fill" style={{ width: `${progress * 100}%` }} />
          </div>
        )}
      </div>
    </li>
  );
}
