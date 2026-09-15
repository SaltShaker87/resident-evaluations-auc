import { useCallback, useEffect, useState } from 'react';
import {
  detectSystem,
  recommend,
  readState,
  runAction,
  subscribe,
} from './backend.js';
import { defaultChoices, trimmedNgcKey } from './utils.js';
import Footer from './components/Footer.jsx';
import Welcome from './screens/Welcome.jsx';
import Checking from './screens/Checking.jsx';
import Choices from './screens/Choices.jsx';
import Installing from './screens/Installing.jsx';
import Done from './screens/Done.jsx';
import ErrorScreen from './screens/ErrorScreen.jsx';
import Manage from './screens/Manage.jsx';
import UninstallConfirm from './screens/UninstallConfirm.jsx';
import FinishNemotron from './screens/FinishNemotron.jsx';

function buildInstallOptions(choices) {
  return {
    ai_enabled: choices.ai_enabled,
    model: choices.ai_enabled ? choices.model : null,
    nemotron_enabled: choices.ai_enabled && choices.nemotron_enabled,
    ngc_key:
      choices.ai_enabled && choices.nemotron_enabled && !choices.ngc_key_later
        ? trimmedNgcKey(choices.ngc_key)
        : null,
    network_scope: choices.network_scope,
    adopt_existing: choices.adopt_existing,
  };
}

export default function App() {
  const [screen, setScreen] = useState('init');
  const [system, setSystem] = useState(null);
  const [recommendation, setRecommendation] = useState(null);
  const [installerState, setInstallerState] = useState(null);
  const [choices, setChoices] = useState(null);
  const [steps, setSteps] = useState({});
  const [stepOrder, setStepOrder] = useState([]);
  const [logLines, setLogLines] = useState([]);
  const [outcome, setOutcome] = useState(null);
  const [preflight, setPreflight] = useState(null);
  const [footerLogOpen, setFooterLogOpen] = useState(false);
  const [finishNemotronKey, setFinishNemotronKey] = useState({ ngc_key: null, ngc_key_later: false });
  const [initError, setInitError] = useState(null);

  const resetRunState = useCallback(() => {
    setSteps({});
    setStepOrder([]);
    setLogLines([]);
    setOutcome(null);
    setPreflight(null);
  }, []);

  const startAction = useCallback(async (action) => {
    resetRunState();
    setScreen('installing');
    try {
      await runAction(action);
    } catch (e) {
      setOutcome({
        ok: false,
        cancelled: false,
        summary: '',
        warnings: [],
        app_url: null,
        nemotron_pending: false,
        error: { message: String(e), hint: null, details: null },
      });
      setScreen('error');
    }
  }, [resetRunState]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const sys = await detectSystem();
        const rec = await recommend(sys);
        const state = await readState();
        if (cancelled) return;
        setSystem(sys);
        setRecommendation(rec);
        setInstallerState(state);
        setChoices(defaultChoices(rec, sys));
        if (sys.existing) {
          setScreen('manage');
        } else if (!sys.supported) {
          setScreen('welcome');
        } else {
          setScreen('welcome');
        }
      } catch (e) {
        if (!cancelled) {
          setInitError(String(e));
          setScreen('error');
          setOutcome({
            ok: false,
            cancelled: false,
            summary: '',
            warnings: [],
            app_url: null,
            nemotron_pending: false,
            error: { message: 'Could not read this computer.', hint: String(e), details: null },
          });
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const unsub = subscribe({
      onStep: (ev) => {
        setSteps((prev) => ({ ...prev, [ev.id]: ev }));
        setStepOrder((prev) => (prev.includes(ev.id) ? prev : [...prev, ev.id]));
      },
      onLog: ({ line }) => {
        setLogLines((prev) => [...prev, line]);
      },
      onPreflight: (payload) => setPreflight(payload),
      onFinished: (result) => {
        setOutcome(result);
        if (result.cancelled) {
          setScreen(choices ? 'choices' : 'manage');
          return;
        }
        if (!result.ok || result.error) {
          setScreen('error');
        } else {
          setScreen('done');
        }
      },
    });
    return unsub;
  }, [choices]);

  const orderedSteps = stepOrder.map((id) => steps[id]).filter(Boolean);

  if (screen === 'init') {
    return (
      <div className="app-shell">
        <main className="screen">
          <p className="muted">Loading…</p>
        </main>
      </div>
    );
  }

  if (screen === 'error' && initError && !system) {
    return (
      <div className="app-shell">
        <main className="screen">
          <ErrorScreen
            outcome={outcome}
            onTryAgain={() => window.location.reload()}
          />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <main className="screen">
        {screen === 'welcome' && (
          <Welcome
            system={system}
            onContinue={() => setScreen('checking')}
          />
        )}
        {screen === 'checking' && (
          <Checking
            system={system}
            recommendation={recommendation}
            choices={choices}
            onContinue={() => setScreen('choices')}
            onBack={() => setScreen('welcome')}
          />
        )}
        {screen === 'choices' && (
          <Choices
            system={system}
            recommendation={recommendation}
            choices={choices}
            onChange={setChoices}
            onBack={() => setScreen('checking')}
            onInstall={() =>
              startAction({ kind: 'install', options: buildInstallOptions(choices) })
            }
          />
        )}
        {screen === 'installing' && (
          <Installing
            stepList={orderedSteps}
            logLines={logLines}
            onCancel={() => setScreen(choices ? 'choices' : 'manage')}
          />
        )}
        {screen === 'done' && (
          <Done
            outcome={outcome}
            preflight={preflight}
            onFinishNemotron={() => {
              setFinishNemotronKey({ ngc_key: null, ngc_key_later: false });
              setScreen('finish_nemotron');
            }}
          />
        )}
        {screen === 'error' && (
          <ErrorScreen
            outcome={outcome}
            onTryAgain={() => setScreen('choices')}
          />
        )}
        {screen === 'manage' && (
          <Manage
            system={system}
            state={installerState}
            onUpdate={() => startAction({ kind: 'update' })}
            onRepair={() => startAction({ kind: 'repair' })}
            onUninstall={() => setScreen('uninstall_confirm')}
            onFinishNemotron={() => {
              setFinishNemotronKey({ ngc_key: null, ngc_key_later: false });
              setScreen('finish_nemotron');
            }}
            onFreshInstall={() => {
              setChoices(defaultChoices(recommendation, system));
              setScreen('checking');
            }}
          />
        )}
        {screen === 'uninstall_confirm' && (
          <UninstallConfirm
            onCancel={() => setScreen('manage')}
            onConfirm={(flags) =>
              startAction({
                kind: 'uninstall',
                delete_data: flags.delete_data,
                delete_nemotron_cache: flags.delete_nemotron_cache,
              })
            }
          />
        )}
        {screen === 'finish_nemotron' && (
          <FinishNemotron
            ngcKey={finishNemotronKey.ngc_key}
            ngcLater={finishNemotronKey.ngc_key_later}
            onChange={(patch) => setFinishNemotronKey((k) => ({ ...k, ...patch }))}
            onBack={() => setScreen(outcome?.ok ? 'done' : 'manage')}
            onRun={() =>
              startAction({
                kind: 'finish_nemotron',
                ngc_key: finishNemotronKey.ngc_key_later
                  ? null
                  : trimmedNgcKey(finishNemotronKey.ngc_key),
              })
            }
          />
        )}
      </main>
      <Footer
        detailsOpen={footerLogOpen}
        onToggleDetails={() => setFooterLogOpen((o) => !o)}
        logLines={logLines}
      />
    </div>
  );
}
