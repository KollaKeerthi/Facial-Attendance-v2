import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertTriangle,
  BadgePlus,
  Camera,
  Clock,
  Eraser,
  Pencil,
  LogIn,
  LogOut,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

async function api(path, options) {
  const headers = options?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

function fmtTime(value) {
  if (!value) return '-';
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value));
}

function fmtPct(value) {
  if (value == null) return '-';
  return `${Math.round(value * 100)}%`;
}

function App() {
  const [tab, setTab] = useState('today');
  const [attendance, setAttendance] = useState({ rows: [] });
  const [employees, setEmployees] = useState([]);
  const [events, setEvents] = useState({ rows: [] });
  const [spoofs, setSpoofs] = useState({ rows: [] });
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function loadAll() {
    setLoading(true);
    setError('');
    try {
      const [today, staff, verify, spoof] = await Promise.all([
        api('/api/attendance/today'),
        api('/api/employees'),
        api('/api/events/today?verification_only=true'),
        api('/api/spoof-attempts/today'),
      ]);
      setAttendance(today);
      setEmployees(staff);
      setEvents(verify);
      setSpoofs(spoof);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
    const id = window.setInterval(loadAll, 15000);
    return () => window.clearInterval(id);
  }, []);

  const filteredAttendance = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return attendance.rows || [];
    return (attendance.rows || []).filter((row) =>
      [row.name, row.employee_code, row.department, row.role]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(needle)),
    );
  }, [attendance.rows, query]);

  const stats = useMemo(() => {
    const rows = attendance.rows || [];
    return {
      total: rows.length,
      inside: rows.filter((row) => row.status === 'inside').length,
      completed: rows.filter((row) => row.in_time && row.out_time).length,
      pending: rows.filter((row) => !row.in_time).length,
    };
  }, [attendance.rows]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <ShieldCheck size={28} />
          <div>
            <strong>Attendance</strong>
            <span>Office access</span>
          </div>
        </div>
        <nav>
          <button className={tab === 'today' ? 'active' : ''} onClick={() => setTab('today')}>
            <Clock size={18} /> Today
          </button>
          <button className={tab === 'employees' ? 'active' : ''} onClick={() => setTab('employees')}>
            <Users size={18} /> Employees
          </button>
          <button className={tab === 'verify' ? 'active' : ''} onClick={() => setTab('verify')}>
            <AlertTriangle size={18} /> Verification
          </button>
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <h1>{tab === 'today' ? "Today's Attendance" : tab === 'employees' ? 'Employees' : 'HR Verification'}</h1>
            <p>{new Date().toLocaleDateString(undefined, { dateStyle: 'full' })}</p>
          </div>
          <button className="iconButton" onClick={loadAll} title="Refresh">
            <RefreshCw size={18} className={loading ? 'spin' : ''} />
          </button>
        </header>

        {error && <div className="error">{error}</div>}

        {tab === 'today' && (
          <TodayView
            stats={stats}
            query={query}
            setQuery={setQuery}
            rows={filteredAttendance}
            onCleared={loadAll}
          />
        )}
        {tab === 'employees' && <EmployeesView employees={employees} onSaved={loadAll} />}
        {tab === 'verify' && <VerificationView events={events.rows || []} spoofs={spoofs.rows || []} />}
      </main>
    </div>
  );
}

function TodayView({ stats, query, setQuery, rows, onCleared }) {
  async function clearToday() {
    if (!window.confirm("Clear today's attendance, events, and spoof attempts? Employees will stay.")) return;
    await api('/api/admin/clear-data?scope=today', { method: 'POST' });
    await onCleared();
  }

  return (
    <>
      <section className="stats">
        <Metric label="Employees" value={stats.total} icon={<Users size={18} />} />
        <Metric label="Inside" value={stats.inside} icon={<LogIn size={18} />} />
        <Metric label="Completed" value={stats.completed} icon={<LogOut size={18} />} />
        <Metric label="Not Arrived" value={stats.pending} icon={<Clock size={18} />} />
      </section>

      <section className="toolbar split">
        <div className="searchBox">
          <Search size={18} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search employee" />
        </div>
        <button className="dangerButton" onClick={clearToday}>
          <Eraser size={16} /> Clear Today
        </button>
      </section>

      <section className="tablePanel">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Department</th>
              <th>Status</th>
              <th>In Time</th>
              <th>Out Time</th>
              <th>Time Spent</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.employee_id}>
                <td>
                  <strong>{row.name}</strong>
                  <span>{row.employee_code || row.gallery_label}</span>
                </td>
                <td>{row.department || '-'}</td>
                <td><Status status={row.status} inTime={row.in_time} /></td>
                <td>{fmtTime(row.in_time)}</td>
                <td>{fmtTime(row.out_time)}</td>
                <td>{row.duration_hhmm || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function EmployeesView({ employees, onSaved }) {
  const [form, setForm] = useState({
    employee_code: '',
    name: '',
    department: '',
    role: '',
    gallery_label: '',
  });
  const [imageFile, setImageFile] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      const created = await api('/api/employees', {
        method: 'POST',
        body: JSON.stringify(form),
      });
      if (imageFile) {
        const data = new FormData();
        data.append('file', imageFile);
        await api(`/api/employees/${created.id}/image`, {
          method: 'POST',
          body: data,
        });
      }
      setForm({ employee_code: '', name: '', department: '', role: '', gallery_label: '' });
      setImageFile(null);
      setMessage(imageFile ? 'Employee and image added. Restart recognition worker.' : 'Employee added');
      await onSaved();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  function beginEdit(row) {
    setEditingId(row.id);
    setEditForm({
      employee_code: row.employee_code || '',
      name: row.name || '',
      department: row.department || '',
      role: row.role || '',
      gallery_label: row.gallery_label || '',
      active: row.active,
    });
    setMessage('');
  }

  async function saveEdit(id) {
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/employees/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(editForm),
      });
      setEditingId(null);
      await onSaved();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function deleteEmployee(id) {
    if (!window.confirm('Remove this employee from active attendance?')) return;
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/employees/${id}`, { method: 'DELETE' });
      await onSaved();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function uploadImage(id, file) {
    if (!file) return;
    const data = new FormData();
    data.append('file', file);
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/employees/${id}/image`, {
        method: 'POST',
        body: data,
      });
      setMessage('Image uploaded. Restart recognition worker to rebuild gallery.');
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="employeesGrid">
      <form className="employeeForm" onSubmit={submit}>
        <h2><BadgePlus size={18} /> Add Employee</h2>
        <label>Employee ID<input value={form.employee_code} onChange={(e) => update('employee_code', e.target.value)} required /></label>
        <label>Name<input value={form.name} onChange={(e) => update('name', e.target.value)} required /></label>
        <label>Department<input value={form.department} onChange={(e) => update('department', e.target.value)} /></label>
        <label>Role<input value={form.role} onChange={(e) => update('role', e.target.value)} /></label>
        <label>Gallery Label<input value={form.gallery_label} onChange={(e) => update('gallery_label', e.target.value)} required /></label>
        <label>Face Image<input type="file" accept="image/png,image/jpeg" onChange={(e) => setImageFile(e.target.files?.[0] || null)} /></label>
        <button type="submit" disabled={saving}>{saving ? 'Saving...' : 'Add Employee'}</button>
        {message && <p className="formMessage">{message}</p>}
      </form>

      <section className="tablePanel">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Department</th>
              <th>Role</th>
              <th>Gallery Label</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {employees.map((row) => (
              <tr key={row.id}>
                {editingId === row.id ? (
                  <>
                    <td>
                      <input className="cellInput" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
                      <input className="cellInput small" value={editForm.employee_code} onChange={(e) => setEditForm({ ...editForm, employee_code: e.target.value })} />
                    </td>
                    <td><input className="cellInput" value={editForm.department} onChange={(e) => setEditForm({ ...editForm, department: e.target.value })} /></td>
                    <td><input className="cellInput" value={editForm.role} onChange={(e) => setEditForm({ ...editForm, role: e.target.value })} /></td>
                    <td><input className="cellInput" value={editForm.gallery_label} onChange={(e) => setEditForm({ ...editForm, gallery_label: e.target.value })} /></td>
                    <td className="actions">
                      <button title="Save" onClick={() => saveEdit(row.id)} disabled={saving}><Save size={16} /></button>
                      <button title="Cancel" onClick={() => setEditingId(null)}><X size={16} /></button>
                    </td>
                  </>
                ) : (
                  <>
                    <td>
                      <strong>{row.name}</strong>
                      <span>{row.employee_code}</span>
                    </td>
                    <td>{row.department || '-'}</td>
                    <td>{row.role || '-'}</td>
                    <td>{row.gallery_label}</td>
                    <td className="actions">
                      <label className="uploadButton" title="Upload face image">
                        <Camera size={16} />
                        <input type="file" accept="image/png,image/jpeg" onChange={(e) => uploadImage(row.id, e.target.files?.[0])} />
                      </label>
                      <button title="Edit" onClick={() => beginEdit(row)}><Pencil size={16} /></button>
                      <button title="Delete" onClick={() => deleteEmployee(row.id)}><Trash2 size={16} /></button>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function VerificationView({ events, spoofs }) {
  return (
    <div className="verifyGrid">
      <SnapshotList title="Low Confidence / Unmatched Events" items={events} type="event" />
      <SnapshotList title="Spoof Attempts" items={spoofs} type="spoof" />
    </div>
  );
}

function SnapshotList({ title, items, type }) {
  return (
    <section className="snapshotPanel">
      <h2>{type === 'spoof' ? <Camera size={18} /> : <AlertTriangle size={18} />} {title}</h2>
      {items.length === 0 && <p className="empty">No items need review.</p>}
      <div className="snapshotList">
        {items.map((item) => (
          <article className="snapshotCard" key={`${type}-${item.id}`}>
            {item.snapshot_url ? (
              <img src={`${API_BASE}${item.snapshot_url}`} alt="" />
            ) : (
              <div className="missingImage">No image</div>
            )}
            <div>
              <strong>{item.name || item.camera_name || 'Unknown'}</strong>
              <span>{fmtTime(item.event_time)}</span>
              <span>{item.direction?.toUpperCase()} {item.verification_reason || item.event_type || ''}</span>
              {item.confidence != null && <span>Confidence {fmtPct(item.confidence)}</span>}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function Metric({ label, value, icon }) {
  return (
    <div className="metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Status({ status, inTime }) {
  const value = status || (inTime ? 'outside' : 'not-arrived');
  return <span className={`status ${value}`}>{value.replace('-', ' ')}</span>;
}

createRoot(document.getElementById('root')).render(<App />);
