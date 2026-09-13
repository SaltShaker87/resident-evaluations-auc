import CheckRow from '../components/CheckRow.jsx';

function osLabel(system) {
  if (system.distro?.pretty) return system.distro.pretty;
  if (system.os === 'linux') return 'Linux';
  if (system.os === 'macos') return 'macOS';
  if (system.os === 'windows') return 'Windows';
  return system.os;
}

function modelExtraGb(modelName) {
  if (!modelName) return 0;
  if (modelName.includes('4b')) return 3;
  if (modelName.includes('9b')) return 6;
  if (modelName.includes('nemotron')) return 0;
  return 4;
}

export default function Checking({ system, recommendation, choices, onContinue, onBack }) {
  const rows = [];
  let blocked = false;

  rows.push({
    tone: 'green',
    text: `Operating system: ${osLabel(system)}.`,
  });

  rows.push({
    tone: 'green',
    text: `Processor architecture: ${system.arch === 'aarch64' ? 'ARM64' : '64-bit Intel/AMD'}.`,
  });

  if (system.gpu.vendor === 'nvidia' && system.gpu.name) {
    const mem = system.gpu.memory_gb != null ? `${system.gpu.memory_gb} GB graphics card memory` : 'graphics memory unknown';
    rows.push({ tone: 'green', text: `Graphics card: ${system.gpu.name} (${mem}).` });
  } else if (system.gpu.vendor === 'none') {
    rows.push({
      tone: 'amber',
      text: 'No NVIDIA graphics card was detected. AI summaries will be turned off unless you enable them anyway.',
    });
  } else {
    rows.push({
      tone: 'amber',
      text: `Graphics: ${system.gpu.name || system.gpu.vendor}. AI features may be limited.`,
    });
  }

  const extra = choices?.ai_enabled ? modelExtraGb(choices.model) : 0;
  const needGb = 3 + extra;
  if (system.disk_free_gb < 3) {
    blocked = true;
    rows.push({
      tone: 'red',
      text: `Free disk space is about ${system.disk_free_gb.toFixed(0)} GB. At least 3 GB is required.`,
    });
  } else if (choices?.ai_enabled && system.disk_free_gb < needGb) {
    rows.push({
      tone: 'amber',
      text: `Free disk space is about ${system.disk_free_gb.toFixed(0)} GB. You may need roughly ${needGb} GB with the chosen AI model.`,
    });
  } else {
    rows.push({
      tone: 'green',
      text: extra > 0
        ? `Free disk space is about ${system.disk_free_gb.toFixed(0)} GB (about 3 GB is needed, plus roughly ${extra} GB for the chosen AI model).`
        : `Free disk space is about ${system.disk_free_gb.toFixed(0)} GB (about 3 GB is needed, plus a few more for AI models if you turn them on).`,
    });
  }

  if (!system.internet) {
    blocked = true;
    rows.push({ tone: 'red', text: 'No internet connection. The installer needs internet to download AUC.' });
  } else {
    rows.push({ tone: 'green', text: 'Internet connection is working.' });
  }

  if (choices?.ai_enabled ?? recommendation.ai.recommended) {
    if (system.tools.ollama.present) {
      const ver = system.tools.ollama.version ? ` (version ${system.tools.ollama.version})` : '';
      rows.push({
        tone: system.tools.ollama.running ? 'green' : 'amber',
        text: system.tools.ollama.running
          ? `Ollama is installed and running${ver}. Ollama is the program that runs AI models on this computer.`
          : `Ollama is installed but not running${ver}. The installer can start it for you.`,
      });
    } else {
      rows.push({
        tone: 'amber',
        text: 'Ollama is not installed yet. Ollama is the program that runs AI models on this computer; the installer will add it.',
      });
    }
  }

  const showDocker =
    recommendation.nemotron.mode !== 'unavailable' &&
    (choices?.nemotron_enabled ?? recommendation.nemotron.mode === 'default');
  if (showDocker) {
    if (system.tools.docker.present && system.tools.docker.compose) {
      rows.push({
        tone: system.tools.docker.usable_by_user ? 'green' : 'amber',
        text: system.tools.docker.usable_by_user
          ? 'Docker is ready for NVIDIA Nemotron.'
          : 'Docker is installed but may need a password or logout before you can use it.',
      });
    } else {
      rows.push({
        tone: 'amber',
        text: 'Docker is not set up yet. It is needed for NVIDIA Nemotron summaries.',
      });
    }
  }

  return (
    <div className="screen-content">
      <h1>Checking your computer</h1>
      <p className="lead">Here is what we found. Amber items are warnings; red items must be fixed before you continue.</p>
      <ul className="check-list">
        {rows.map((r, i) => (
          <CheckRow key={i} tone={r.tone}>{r.text}</CheckRow>
        ))}
      </ul>
      <div className="screen-actions screen-actions--split">
        <button type="button" className="btn btn--secondary" onClick={onBack}>
          Back
        </button>
        <button
          type="button"
          className="btn btn--primary"
          disabled={blocked}
          onClick={onContinue}
        >
          Continue
        </button>
      </div>
    </div>
  );
}
