export default function Welcome({ system, onContinue }) {
  if (!system.supported) {
    return (
      <div className="screen-content">
        <h1>AUC cannot be installed on this computer</h1>
        <p className="lead">{system.unsupported_reason}</p>
      </div>
    );
  }

  return (
    <div className="screen-content">
      <div className="brand-mark" aria-hidden>AUC</div>
      <h1>Welcome to AUC</h1>
      <p className="lead">
        AUC (Assessments Under Curve) helps residency programs track resident evaluations
        and generate summary reports. Everything stays on this computer unless you choose
        to share access on your network.
      </p>
      <p>
        This installer will download AUC, set up anything it needs to run, and start it
        for you.
      </p>
      <div className="screen-actions">
        <button type="button" className="btn btn--primary btn--lg" onClick={onContinue}>
          Continue
        </button>
      </div>
    </div>
  );
}
