import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Check, AlertCircle, AlertTriangle, Clock } from 'lucide-react';
import { getAllFollowups, resolveFollowup } from '../api';

const priorityIcon = {
  urgent: <AlertCircle size={14} style={{ color: 'var(--red-500)' }} />,
  important: <AlertTriangle size={14} style={{ color: 'var(--amber-500)' }} />,
  routine: <Clock size={14} style={{ color: 'var(--slate-400)' }} />,
};

function formatDate(dateStr) {
  if (!dateStr) return '';
  const iso = dateStr.replace(' ', 'T');
  const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function FollowupDashboard({ showToast }) {
  const [followups, setFollowups] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getAllFollowups(false);
      setFollowups(data);
    } catch {
      showToast('Failed to load follow-ups');
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleResolve = async (id) => {
    await resolveFollowup(id);
    showToast('Follow-up resolved');
    load();
  };

  if (loading) {
    return <div className="loading-state"><div className="spinner" /><span>Loading follow-ups…</span></div>;
  }

  const urgent = followups.filter((f) => f.priority === 'urgent');
  const important = followups.filter((f) => f.priority === 'important');
  const routine = followups.filter((f) => f.priority === 'routine');

  const renderGroup = (title, items, icon) => {
    if (items.length === 0) return null;
    return (
      <div className="section">
        <div className="section__header">
          <div className="section__title flex items-center gap-sm">{icon} {title}</div>
          <span className="badge badge--count">{items.length}</span>
        </div>
        <div className="timeline">
          {items.map((f) => (
            <div key={f.id} className="timeline-item">
              <div className="timeline-item__header">
                <Link
                  to={`/residents/${f.resident_id}`}
                  style={{ fontWeight: 600, color: 'var(--blue-600)', textDecoration: 'none' }}
                >
                  Dr. {f.first_name} {f.last_name}
                </Link>
                <span className="tag tag--pgy">PGY-{f.pgy_year}</span>
                <span className="timeline-item__date">{formatDate(f.created_at)}</span>
              </div>
              <div className="timeline-item__content">{f.description}</div>
              <div className="mt-sm">
                <button className="btn btn--secondary btn--sm" onClick={() => handleResolve(f.id)}>
                  <Check size={14} /> Mark Resolved
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="page-header">
        <h1>Follow-Up Dashboard</h1>
        <p>{followups.length} open item{followups.length !== 1 ? 's' : ''} across all residents</p>
      </div>

      {followups.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state__icon"><Check size={48} /></div>
          <h3>All clear</h3>
          <p>No open follow-up items.</p>
        </div>
      ) : (
        <>
          {renderGroup('Urgent', urgent, <AlertCircle size={18} style={{ color: 'var(--red-500)' }} />)}
          {renderGroup('Important', important, <AlertTriangle size={18} style={{ color: 'var(--amber-500)' }} />)}
          {renderGroup('Routine', routine, <Clock size={18} style={{ color: 'var(--slate-400)' }} />)}
        </>
      )}
    </>
  );
}
