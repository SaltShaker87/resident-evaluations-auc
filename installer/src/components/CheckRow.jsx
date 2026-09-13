import { AlertTriangle, Check, X } from 'lucide-react';

export default function CheckRow({ tone, children }) {
  const Icon = tone === 'red' ? X : tone === 'amber' ? AlertTriangle : Check;
  return (
    <li className={`check-row check-row--${tone}`}>
      <Icon size={18} aria-hidden className="check-row__icon" />
      <span>{children}</span>
    </li>
  );
}
