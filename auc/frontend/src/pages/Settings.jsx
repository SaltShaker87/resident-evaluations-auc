import React, { useState, useEffect } from 'react';
import { NavLink, Navigate, useParams } from 'react-router-dom';
import { Palette, Sparkles, Users, FileInput, GraduationCap, HardDrive, RefreshCw, Info } from 'lucide-react';
import { getActiveSnapshot } from '../api';
import AppearanceSection from '../components/settings/AppearanceSection';
import AiSection from '../components/settings/AiSection';
import AdvisorsCard from '../components/AdvisorsCard';
import UpdatesCard from '../components/UpdatesCard';
import MedhubSettingsCards from '../components/MedhubSettingsCards';
import AcademicYearSection from '../components/settings/AcademicYearSection';
import BackupSection from '../components/settings/BackupSection';
import AboutSection from '../components/settings/AboutSection';

// Each category has its own address (/settings/<key>), so Back and a refresh
// keep you where you were. The first one is where /settings lands.
const SECTIONS = [
  { key: 'appearance', label: 'Appearance', icon: Palette,
    desc: 'How AUC looks on this computer.' },
  { key: 'ai', label: 'AI & Summaries', icon: Sparkles,
    desc: 'The model that writes summaries and the ACGME reference it draws on.' },
  { key: 'advisors', label: 'Advisors', icon: Users,
    desc: 'The list behind the Advisor dropdown on each resident page.' },
  { key: 'medhub', label: 'MedHub Import', icon: FileInput,
    desc: 'How MedHub evaluation questions are sorted, and which names an import skips.' },
  { key: 'academic-year', label: 'Academic Year', icon: GraduationCap,
    desc: 'Move every resident on to the next year, or undo the last time you did.' },
  { key: 'backup', label: 'Backup & Data', icon: HardDrive,
    desc: 'Keep a copy of everything somewhere safe.' },
  { key: 'updates', label: 'Updates', icon: RefreshCw,
    desc: 'Which version is running, and installing a newer one.' },
  { key: 'about', label: 'About', icon: Info,
    desc: 'Version, credits, and where your data lives.' },
];

export default function Settings({ theme, setTheme, updateAvailable, onUpdateCheck }) {
  const { section } = useParams();
  const current = SECTIONS.find((s) => s.key === section);

  // Loaded here rather than in Academic Year so the list can flag that an
  // advancement can still be undone.
  const [snapshot, setSnapshot] = useState(null);
  const loadSnapshot = () => {
    getActiveSnapshot().then(setSnapshot).catch(() => setSnapshot(null));
  };
  useEffect(() => { loadSnapshot(); }, []);

  if (!current) return <Navigate to={`/settings/${SECTIONS[0].key}`} replace />;

  return (
    <div>
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings categories">
          {SECTIONS.map(({ key, label, icon: Icon }) => (
            <NavLink key={key} to={`/settings/${key}`} className="settings-nav__item">
              <Icon size={16} />
              <span>{label}</span>
              {key === 'academic-year' && snapshot && (
                <span className="settings-nav__dot" title="An advancement can still be undone" />
              )}
              {key === 'updates' && updateAvailable && (
                <span className="settings-nav__dot" title="An update is available" />
              )}
            </NavLink>
          ))}
        </nav>

        <section className="settings-panel">
          <div className="settings-panel__header">
            <h2>{current.label}</h2>
            <p>{current.desc}</p>
          </div>

          {current.key === 'appearance' && <AppearanceSection theme={theme} setTheme={setTheme} />}
          {current.key === 'ai' && <AiSection />}
          {current.key === 'advisors' && <AdvisorsCard />}
          {current.key === 'medhub' && <MedhubSettingsCards />}
          {current.key === 'academic-year' && (
            <AcademicYearSection snapshot={snapshot} reloadSnapshot={loadSnapshot} />
          )}
          {current.key === 'backup' && <BackupSection />}
          {current.key === 'updates' && <UpdatesCard onCheck={onUpdateCheck} />}
          {current.key === 'about' && <AboutSection />}
        </section>
      </div>
    </div>
  );
}
