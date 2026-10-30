import React, { useState } from 'react';
import { Download, Check } from 'lucide-react';
import { downloadBackup } from '../../api';

export default function BackupSection() {
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupDone, setBackupDone] = useState(false);
  const [backupError, setBackupError] = useState('');

  const handleBackup = async () => {
    setBackupBusy(true);
    setBackupError('');
    setBackupDone(false);
    try {
      await downloadBackup();
      setBackupDone(true);
      setTimeout(() => setBackupDone(false), 4000);
    } catch (err) {
      setBackupError(err.message || 'Download failed.');
    }
    setBackupBusy(false);
  };

  return (
    <div className="card settings-card">
      <div className="settings-row">
        <div>
          <div className="settings-row__label">Download full backup (.zip)</div>
          <div className="settings-row__desc">
            Downloads a complete copy of your database and all resident photos as a
            single .zip. Keep it somewhere safe. (An automated daily backup can also
            run in the background — see BACKUPS.md.)
          </div>
        </div>
        <div className="settings-row__control" style={{ flexDirection: 'column', alignItems: 'flex-end', gap: '0.5rem' }}>
          <button className="btn btn--primary" onClick={handleBackup} disabled={backupBusy}>
            <Download size={15} /> {backupBusy ? 'Preparing…' : 'Download Backup'}
          </button>
          {backupDone && (
            <span className="text-sm" style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: 'var(--green-600)' }}>
              <Check size={15} /> Download started
            </span>
          )}
          {backupError && (
            <span className="text-sm" style={{ color: 'var(--text-muted)' }}>{backupError}</span>
          )}
        </div>
      </div>
    </div>
  );
}
