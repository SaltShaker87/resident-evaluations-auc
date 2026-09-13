/**
 * Browser dev mock — same commands/events as CONTRACT.md.
 * ?scenario=spark|pc|nogpu|existing|manual|unsupported
 * ?fail=models — fail the models step during run_action
 */

const INSTALL_STEPS = [
  { id: 'prepare', label: 'Getting ready' },
  { id: 'download', label: 'Downloading AUC' },
  { id: 'python', label: 'Setting up Python' },
  { id: 'configure', label: 'Writing settings' },
  { id: 'ollama', label: 'Installing Ollama' },
  { id: 'models', label: 'Downloading AI models' },
  { id: 'docker', label: 'Setting up Docker for NVIDIA Nemotron' },
  { id: 'nemotron', label: 'Starting NVIDIA Nemotron' },
  { id: 'index', label: 'Building the ACGME reference index' },
  { id: 'autostart', label: 'Setting up auto-start' },
  { id: 'start', label: 'Starting AUC' },
  { id: 'preflight', label: 'Checking everything works' },
];

const UPDATE_STEPS = [
  { id: 'prepare', label: 'Getting ready' },
  { id: 'backup', label: 'Backing up your data' },
  { id: 'download', label: 'Downloading AUC' },
  { id: 'python', label: 'Setting up Python' },
  { id: 'configure', label: 'Writing settings' },
  { id: 'nemotron', label: 'Starting NVIDIA Nemotron' },
  { id: 'index', label: 'Building the ACGME reference index' },
  { id: 'switch', label: 'Switching to the new version' },
  { id: 'preflight', label: 'Checking everything works' },
];

const FINISH_NEMOTRON_STEPS = [
  { id: 'prepare', label: 'Getting ready' },
  { id: 'docker', label: 'Setting up Docker for NVIDIA Nemotron' },
  { id: 'nemotron', label: 'Starting NVIDIA Nemotron' },
  { id: 'index', label: 'Building the ACGME reference index' },
  { id: 'configure', label: 'Writing settings' },
  { id: 'start', label: 'Starting AUC' },
  { id: 'preflight', label: 'Checking everything works' },
];

const UNINSTALL_STEPS = [
  { id: 'stop', label: 'Stopping AUC' },
  { id: 'remove_autostart', label: 'Removing auto-start' },
  { id: 'remove_app', label: 'Removing the application' },
  { id: 'remove_data', label: 'Removing your data' },
  { id: 'remove_nemotron_cache', label: 'Removing Nemotron model files' },
];

function scenarioFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get('scenario') || 'spark';
}

function failStepFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get('fail');
}

function baseModels(memoryGb, aiRecommended) {
  const models = [
    {
      name: 'qwen3.5:4b',
      label: 'Qwen 3.5 4B',
      description: 'Smaller model; good when graphics memory is limited.',
      min_memory_gb: 8,
      recommended: false,
      fits: memoryGb >= 8,
    },
    {
      name: 'qwen3.5:9b',
      label: 'Qwen 3.5 9B',
      description: 'Balanced quality for most workstations.',
      min_memory_gb: 16,
      recommended: false,
      fits: memoryGb >= 16,
    },
    {
      name: 'nemotron-3.5-lightning',
      label: 'NVIDIA Nemotron 3.5 Lightning',
      description: 'The best summaries; needs a large graphics card or a DGX Spark.',
      min_memory_gb: 33,
      recommended: false,
      fits: memoryGb >= 33,
    },
  ];
  if (!aiRecommended) {
    return models;
  }
  let recName = 'qwen3.5:4b';
  if (memoryGb >= 33) recName = 'nemotron-3.5-lightning';
  else if (memoryGb >= 16) recName = 'qwen3.5:9b';
  else if (memoryGb >= 8) recName = 'qwen3.5:4b';
  for (const m of models) {
    m.recommended = m.name === recName;
  }
  return models;
}

function recommendForSystem(system) {
  const mem = system.gpu.memory_gb ?? 0;
  const hasGpu = system.gpu.vendor === 'nvidia' && mem >= 8;
  const aiRecommended = hasGpu && mem >= 8;
  const models = baseModels(mem, aiRecommended);

  let bestMessage =
    'AUC runs best on NVIDIA Nemotron 3.5 Lightning.';
  if (mem < 33) {
    bestMessage += ` It needs more than 32 GB of graphics card memory; this machine has ${mem || 0} GB.`;
  }

  let nemotron = { mode: 'unavailable', reason: 'Nemotron needs a Linux computer with an NVIDIA graphics card.' };
  if (system.os === 'linux' && system.gpu.vendor === 'nvidia') {
    if (system.gpu.is_gb10) {
      nemotron = { mode: 'default', reason: '' };
    } else if (mem >= 16) {
      nemotron = { mode: 'optional', reason: '' };
    } else {
      nemotron = {
        mode: 'unavailable',
        reason: 'Your graphics card does not have enough memory for Nemotron (16 GB or more is needed).',
      };
    }
  }

  return {
    ai: {
      recommended: aiRecommended,
      reason: aiRecommended
        ? 'Your graphics card can run local AI models for summaries.'
        : 'This computer does not have enough graphics memory for local AI summaries.',
    },
    models,
    best_message: bestMessage,
    nemotron,
  };
}

function systemsByScenario() {
  const s = scenarioFromUrl();
  const installerState = {
    schema: 1,
    version: '1.3.0',
    installed_at: '2026-08-01T12:00:00Z',
    auc_home: '/home/user/.local/share/auc',
    choices: {
      ai_enabled: true,
      model: 'qwen3.5:9b',
      nemotron_enabled: false,
      network_scope: 'local',
    },
    nemotron_pending: true,
    adopted_from: null,
  };

  const spark = {
    os: 'linux',
    arch: 'aarch64',
    distro: { id: 'ubuntu', version: '24.04', pretty: 'Ubuntu 24.04 LTS' },
    supported: true,
    unsupported_reason: null,
    gpu: { vendor: 'nvidia', name: 'NVIDIA GB10', memory_gb: 128, is_gb10: true },
    memory_gb: 128,
    disk_free_gb: 420,
    internet: true,
    tools: {
      ollama: { present: true, version: '0.5.1', running: true },
      docker: { present: true, usable_by_user: true, nvidia_runtime: true, compose: true },
      systemd_user: true,
      pkexec: true,
    },
    existing: null,
  };

  const pc = {
    os: 'linux',
    arch: 'x86_64',
    distro: { id: 'ubuntu', version: '22.04', pretty: 'Ubuntu 22.04 LTS' },
    supported: true,
    unsupported_reason: null,
    gpu: { vendor: 'nvidia', name: 'NVIDIA GeForce RTX 3090', memory_gb: 24, is_gb10: false },
    memory_gb: 64,
    disk_free_gb: 180,
    internet: true,
    tools: {
      ollama: { present: false, version: null, running: false },
      docker: { present: false, usable_by_user: false, nvidia_runtime: false, compose: false },
      systemd_user: true,
      pkexec: true,
    },
    existing: null,
  };

  const nogpu = {
    os: 'linux',
    arch: 'x86_64',
    distro: { id: 'ubuntu', version: '24.04', pretty: 'Ubuntu 24.04 LTS' },
    supported: true,
    unsupported_reason: null,
    gpu: { vendor: 'none', name: null, memory_gb: null, is_gb10: false },
    memory_gb: 16,
    disk_free_gb: 45,
    internet: true,
    tools: {
      ollama: { present: false, version: null, running: false },
      docker: { present: false, usable_by_user: false, nvidia_runtime: false, compose: false },
      systemd_user: true,
      pkexec: true,
    },
    existing: null,
  };

  const existing = {
    ...pc,
    gpu: { vendor: 'nvidia', name: 'NVIDIA GeForce RTX 3090', memory_gb: 24, is_gb10: false },
    tools: {
      ollama: { present: true, version: '0.5.0', running: true },
      docker: { present: true, usable_by_user: true, nvidia_runtime: true, compose: true },
      systemd_user: true,
      pkexec: true,
    },
    existing: { kind: 'installer', version: '1.3.0', state: installerState },
  };

  const manual = {
    ...pc,
    existing: {
      kind: 'manual',
      service_path: '/home/user/.config/systemd/user/auc.service',
      working_directory: '/home/user/auc/backend',
      env: { AUC_DATA_DIR: '/home/user/auc-data' },
    },
  };

  const unsupported = {
    os: 'macos',
    arch: 'aarch64',
    distro: null,
    supported: false,
    unsupported_reason:
      'This installer currently supports Linux only. AUC on a Mac must be set up manually using the project documentation.',
    gpu: { vendor: 'apple', name: 'Apple M2', memory_gb: null, is_gb10: false },
    memory_gb: 16,
    disk_free_gb: 90,
    internet: true,
    tools: {
      ollama: { present: false, version: null, running: false },
      docker: { present: false, usable_by_user: false, nvidia_runtime: false, compose: false },
      systemd_user: false,
      pkexec: false,
    },
    existing: null,
  };

  const map = { spark, pc, nogpu, existing, manual, unsupported };
  return map[s] || spark;
}

let cachedSystem = null;
let logLines = [];
let listeners = { onStep: null, onLog: null, onPreflight: null, onFinished: null };
let actionRunning = false;
let cancelRequested = false;

function emitLog(line) {
  logLines.push(line);
  listeners.onLog?.({ line });
}

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function stepsForAction(action, options) {
  if (action.kind === 'update') return UPDATE_STEPS;
  if (action.kind === 'repair') return INSTALL_STEPS;
  if (action.kind === 'finish_nemotron') return FINISH_NEMOTRON_STEPS;
  if (action.kind === 'uninstall') return UNINSTALL_STEPS;
  const steps = [...INSTALL_STEPS];
  if (!options?.ai_enabled) {
    return steps.filter((st) => st.id !== 'ollama' && st.id !== 'models');
  }
  if (!options?.nemotron_enabled) {
    return steps.filter((st) => st.id !== 'docker' && st.id !== 'nemotron');
  }
  return steps;
}

async function runSteps(action, options, scenario) {
  const steps = stepsForAction(action, options);
  const failId = failStepFromUrl();

  for (const st of steps) {
    listeners.onStep?.({
      id: st.id,
      label: st.label,
      status: 'pending',
      detail: null,
      progress: null,
    });
  }

  await delay(200);
  let nemotronPending = false;

  for (const st of steps) {
    if (cancelRequested) {
      listeners.onFinished?.({
        ok: false,
        cancelled: true,
        summary: 'The operation was cancelled.',
        warnings: [],
        app_url: null,
        nemotron_pending: nemotronPending,
        error: null,
      });
      actionRunning = false;
      cancelRequested = false;
      return;
    }

    listeners.onStep?.({
      id: st.id,
      label: st.label,
      status: 'running',
      detail: null,
      progress: null,
    });
    emitLog(`[mock] ${st.label} — started`);

    if (st.id === 'models') {
      const total = 5.6;
      for (let p = 0; p <= 10; p++) {
        await delay(120);
        const done = (total * p) / 10;
        listeners.onStep?.({
          id: st.id,
          label: st.label,
          status: 'running',
          detail: `qwen3.5:9b — ${done.toFixed(1)} GB of ${total} GB`,
          progress: p / 10,
        });
      }
      if (failId === 'models') {
        listeners.onStep?.({
          id: st.id,
          label: st.label,
          status: 'failed',
          detail: 'Download interrupted',
          progress: null,
        });
        emitLog('[mock] models pull failed: connection reset');
        listeners.onFinished?.({
          ok: false,
          cancelled: false,
          summary: '',
          warnings: [],
          app_url: null,
          nemotron_pending: false,
          error: {
            message: 'Could not download the AI model.',
            hint: 'Check your internet connection and try again.',
            details: null,
          },
        });
        actionRunning = false;
        return;
      }
    } else if (st.id === 'nemotron' && scenario === 'spark') {
      await delay(600);
      nemotronPending = true;
      listeners.onStep?.({
        id: st.id,
        label: st.label,
        status: 'warning',
        detail: 'Nemotron is not ready yet; summaries will use the Standard engine.',
        progress: null,
      });
      emitLog('[mock] nemotron health check timed out — will finish later');
      continue;
    } else if (st.id === 'remove_data' && !options?.delete_data) {
      listeners.onStep?.({
        id: st.id,
        label: st.label,
        status: 'skipped',
        detail: 'Your data was kept.',
        progress: null,
      });
      continue;
    } else if (st.id === 'remove_nemotron_cache' && !options?.delete_nemotron_cache) {
      listeners.onStep?.({
        id: st.id,
        label: st.label,
        status: 'skipped',
        detail: 'Nemotron model files were kept.',
        progress: null,
      });
      continue;
    } else if (st.id === 'ollama' && !options?.ai_enabled) {
      listeners.onStep?.({
        id: st.id,
        label: st.label,
        status: 'skipped',
        detail: 'AI summaries are turned off.',
        progress: null,
      });
      continue;
    } else {
      await delay(350 + Math.random() * 200);
    }

    if (st.id === 'preflight') {
      listeners.onPreflight?.({
        lines: [
          { level: 'pass', text: 'AUC web server is responding.', hint: null },
          { level: 'pass', text: 'Database file is readable.', hint: null },
          { level: 'info', text: 'Ollama is running.', hint: null },
          ...(nemotronPending
            ? [{
              level: 'warn',
              text: 'Nemotron is not running; using Standard retrieval.',
              hint: 'Finish Nemotron setup from the installer when you have your NVIDIA key.',
            }]
            : [{ level: 'pass', text: 'Retrieval engine is healthy.', hint: null }]),
        ],
      });
    }

    listeners.onStep?.({
      id: st.id,
      label: st.label,
      status: 'done',
      detail: null,
      progress: null,
    });
    emitLog(`[mock] ${st.label} — done`);
  }

  const isUninstall = action.kind === 'uninstall';
  listeners.onFinished?.({
    ok: true,
    cancelled: false,
    summary: isUninstall
      ? 'AUC has been removed. Ollama and Docker were left on your computer in case other programs use them.'
      : 'AUC is installed and running. You can open it in your web browser.',
    warnings: nemotronPending
      ? ['AI summaries will use the Standard engine until Nemotron setup is finished.']
      : [],
    app_url: isUninstall ? null : 'http://localhost:3000',
    nemotron_pending: nemotronPending,
    error: null,
  });
  actionRunning = false;
}

export function mockDetectSystem() {
  cachedSystem = systemsByScenario();
  return Promise.resolve(cachedSystem);
}

export function mockRecommend(system) {
  return Promise.resolve(recommendForSystem(system));
}

export function mockReadState() {
  const sys = cachedSystem || systemsByScenario();
  if (sys.existing?.kind === 'installer') {
    return Promise.resolve(sys.existing.state);
  }
  return Promise.resolve(null);
}

export function mockCheckForUpdate() {
  const sys = cachedSystem || systemsByScenario();
  if (scenarioFromUrl() === 'existing' || sys.existing?.kind === 'installer') {
    return Promise.resolve({
      installed: '1.3.0',
      latest: '1.4.0',
      available: true,
      notes: 'Bug fixes and improved summaries.',
      error: null,
    });
  }
  return Promise.resolve({
    installed: null,
    latest: '1.4.0',
    available: false,
    notes: null,
    error: null,
  });
}

export function mockRunAction(action) {
  if (actionRunning) {
    return Promise.reject('An operation is already running.');
  }
  actionRunning = true;
  cancelRequested = false;
  logLines = [`[mock] action ${action.kind} started`];

  const options = action.kind === 'install' ? action.options : action;
  const scenario = scenarioFromUrl();

  setTimeout(() => {
    runSteps(action, options, scenario).catch((e) => {
      actionRunning = false;
      listeners.onFinished?.({
        ok: false,
        cancelled: false,
        summary: '',
        warnings: [],
        app_url: null,
        nemotron_pending: false,
        error: { message: String(e), hint: null, details: null },
      });
    });
  }, 50);

  return Promise.resolve();
}

export function mockCancelAction() {
  cancelRequested = true;
  return Promise.resolve();
}

export function mockGetLog() {
  return Promise.resolve(logLines.join('\n'));
}

export function mockOpenApp() {
  window.open('http://localhost:3000', '_blank');
  return Promise.resolve();
}

export function mockOpenUrl(url) {
  window.open(url, '_blank');
  return Promise.resolve();
}

export function mockSubscribe(handlers) {
  listeners = { ...listeners, ...handlers };
  return () => {
    listeners = { onStep: null, onLog: null, onPreflight: null, onFinished: null };
  };
}
