import packageJson from '../../package.json';

export default function Footer({ detailsOpen, onToggleDetails, logLines }) {
  return (
    <footer className="installer-footer">
      <span className="installer-footer__version">Installer {packageJson.version}</span>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onToggleDetails}
        aria-expanded={detailsOpen}
      >
        {detailsOpen ? 'Hide details' : 'Details'}
      </button>
      {detailsOpen && (
        <div className="installer-footer__log" role="log" aria-live="polite">
          {(logLines.length ? logLines : ['No log lines yet.']).map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      )}
    </footer>
  );
}
