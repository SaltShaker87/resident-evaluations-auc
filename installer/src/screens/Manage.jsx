import { useEffect, useState } from 'react';
import { checkForUpdate } from '../backend.js';

export default function Manage({
  system,
  state,
  onUpdate,
  onRepair,
  onUninstall,
  onFinishNemotron,
  onFreshInstall,
}) {
  const [updateInfo, setUpdateInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    checkForUpdate().then((info) => {
      if (!cancelled) {
        setUpdateInfo(info);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, []);

  const version =
    system.existing?.kind === 'installer'
      ? system.existing.version
      : state?.version || 'unknown';

  return (
    <div className="screen-content">
      <h1>Manage AUC</h1>
      <p className="lead">
        AUC is already on this computer (version {version}).
      </p>

      <div className="manage-actions">
        <section className="card manage-card">
          <h2>Updates</h2>
          {loading && <p className="muted">Checking for updates…</p>}
          {!loading && updateInfo?.available && (
            <>
              <p>Version {updateInfo.latest} is available.</p>
              {updateInfo.notes && <p className="hint">{updateInfo.notes}</p>}
              <button type="button" className="btn btn--primary" onClick={onUpdate}>
                Update
              </button>
            </>
          )}
          {!loading && !updateInfo?.available && (
            <p>You have the newest version.</p>
          )}
          {updateInfo?.error && <p className="muted">{updateInfo.error}</p>}
        </section>

        <section className="card manage-card">
          <h2>Maintenance</h2>
          <p className="hint">Repair re-runs setup steps without deleting your data.</p>
          <button type="button" className="btn btn--secondary" onClick={onRepair}>
            Repair
          </button>
        </section>

        {(state?.nemotron_pending || system.existing?.state?.nemotron_pending) && (
          <section className="card manage-card">
            <h2>Nemotron</h2>
            <p className="hint">Finish downloading and starting NVIDIA Nemotron.</p>
            <button type="button" className="btn btn--secondary" onClick={onFinishNemotron}>
              Finish Nemotron setup
            </button>
          </section>
        )}

        <section className="card manage-card manage-card--danger">
          <h2>Uninstall</h2>
          <button type="button" className="btn btn--danger" onClick={onUninstall}>
            Uninstall AUC
          </button>
        </section>

        {system.existing?.kind === 'manual' && (
          <section className="card manage-card">
            <h2>New install</h2>
            <p className="hint">Run the full installer and optionally move your old data.</p>
            <button type="button" className="btn btn--secondary" onClick={onFreshInstall}>
              Install with migration
            </button>
          </section>
        )}
      </div>
    </div>
  );
}
