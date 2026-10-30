import React, { useState, useEffect } from 'react';
import { ShieldCheck, HardDrive, Cpu, CloudOff, RefreshCw } from 'lucide-react';
import { getVersion } from '../../api';
import aucMark from '../../assets/auc-mark.png';

// Logos are served from public/logos/ rather than imported, so the page still
// builds while a file is missing — the name is shown in its place.
function Logo({ src, alt }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return <img className="about-credit__logo" src={src} alt={alt} onError={() => setFailed(true)} />;
}

export default function AboutSection() {
  // "dev" when running from a checkout; a release writes auc/VERSION.
  const [version, setVersion] = useState(null);

  useEffect(() => {
    getVersion().then((r) => setVersion(r.version)).catch(() => setVersion('unknown'));
  }, []);

  return (
    <>
      <div className="card settings-card about-hero">
        <img className="about-hero__mark" src={aucMark} alt="" />
        <div>
          <div className="about-hero__name">AUC</div>
          <div className="settings-row__desc">Assessments Under Curve</div>
          <div className="about-hero__version">
            {version === null ? 'Checking version…'
              : version === 'dev' ? 'Development build'
              : `Version ${version}`}
          </div>
        </div>
      </div>

      <div className="card settings-card">
        <div className="about-credit">
          <Logo src="/logos/salt-shaker-studio.webp" alt="Salt Shaker Studio" />
          <div>
            <div className="settings-row__desc">Made by</div>
            <div className="settings-row__label">Salt Shaker Studio</div>
          </div>
        </div>
        <div className="about-credit">
          <Logo src="/logos/nvidia.png" alt="NVIDIA" />
          <div>
            <div className="settings-row__desc">In collaboration with</div>
            <div className="settings-row__label">NVIDIA</div>
          </div>
        </div>
      </div>

      <div className="card settings-card about-privacy">
        <div className="about-privacy__head">
          <ShieldCheck size={20} />
          <div className="settings-row__label">Local and private</div>
        </div>
        <p className="settings-row__desc">
          Everything AUC does happens on this computer. Your data does not leave this machine.
        </p>
        <ul className="about-privacy__list">
          <li>
            <HardDrive size={15} />
            <span>Resident records, evaluations, photos and notes are stored in a database on this computer.</span>
          </li>
          <li>
            <Cpu size={15} />
            <span>The AI models that write summaries run here too — no resident data is sent to an AI service.</span>
          </li>
          <li>
            <CloudOff size={15} />
            <span>Nothing is uploaded to a cloud or shared with an outside company, NVIDIA and Salt Shaker Studio included.</span>
          </li>
          <li>
            <RefreshCw size={15} />
            <span>The only thing AUC fetches from the internet is new versions of itself, from GitHub. Checking for one sends no resident data.</span>
          </li>
        </ul>
      </div>
    </>
  );
}
