import aucLogo from '../assets/auc-logo.png';
import nvidiaLogo from '../assets/nvidia-logo.svg';

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
      {/* The logo artwork has its own black background, so it sits in a dark
          panel rather than being cut out of it. */}
      <div className="brand-panel">
        <img src={aucLogo} alt="AUC — Assessments Under Curve" />
      </div>
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
      <p className="partner-row">
        <span>In partnership with</span>
        <img src={nvidiaLogo} alt="NVIDIA" />
      </p>
      <div className="screen-actions">
        <button type="button" className="btn btn--primary btn--lg" onClick={onContinue}>
          Continue
        </button>
      </div>
    </div>
  );
}
