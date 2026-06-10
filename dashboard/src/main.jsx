import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertTriangle,
  BadgePlus,
  BarChart2,
  Building2,
  Camera,
  CheckCircle,
  ChevronDown,
  Clock,
  Eraser,
  LogIn,
  LogOut,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Shield,
  ShieldCheck,
  Trash2,
  UserCheck,
  Users,
  X,
  XCircle,
} from 'lucide-react';
import { api, API_BASE, clearAuth, getStoredUser, setStoredUser, setToken } from './api.js';
import './styles.css';

// ─── Auth Context ─────────────────────────────────────────────────────────────

const AuthCtx = createContext(null);

function useAuth() {
  return useContext(AuthCtx);
}

// ─── Utility helpers ──────────────────────────────────────────────────────────

function fmtTime(value) {
  if (!value) return '-';
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value));
}

function fmtDate(value) {
  if (!value) return '-';
  return new Date(value).toLocaleDateString(undefined, { dateStyle: 'medium' });
}

function fmtPct(value) {
  if (value == null) return '-';
  return `${Math.round(value * 100)}%`;
}

function isoDate(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function todayISO() {
  return isoDate(new Date());
}

function weekStartISO() {
  const d = new Date();
  d.setDate(d.getDate() - d.getDay() + 1);
  return isoDate(d);
}

// ─── Small Shared Components ──────────────────────────────────────────────────

function Metric({ label, value, icon, color }) {
  return (
    <div className="metric">
      <div className="metricIcon" style={color ? { color } : undefined}>{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusBadge({ status, inTime }) {
  const val = status || (inTime ? 'outside' : 'not-arrived');
  return <span className={`status ${val}`}>{val.replace(/-/g, ' ')}</span>;
}

function ApprovalBadge({ status }) {
  const cls = status === 'approved' ? 'approved' : status === 'rejected' ? 'rejected' : 'pending';
  return <span className={`approvalBadge ${cls}`}>{status || 'approved'}</span>;
}

function Spinner() {
  return <RefreshCw size={18} className="spin" />;
}

function ErrorBanner({ msg }) {
  if (!msg) return null;
  return <div className="error">{msg}</div>;
}

function ConfirmButton({ label, className, onConfirm, confirmMsg, children, disabled }) {
  async function handleClick() {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    await onConfirm();
  }
  return (
    <button className={className} onClick={handleClick} disabled={disabled}>
      {children || label}
    </button>
  );
}

// ─── Bar Chart (pure SVG) ────────────────────────────────────────────────────

function BarChart({ data, valueKey, labelKey, color = '#176b4b', height = 120 }) {
  if (!data || data.length === 0) return <p className="empty">No data</p>;
  const max = Math.max(...data.map((d) => d[valueKey] || 0), 1);
  const barW = Math.floor(400 / data.length) - 4;
  return (
    <svg viewBox={`0 0 400 ${height + 28}`} style={{ width: '100%', display: 'block' }}>
      {data.map((d, i) => {
        const val = d[valueKey] || 0;
        const barH = Math.round((val / max) * height);
        const x = i * (400 / data.length) + 2;
        const y = height - barH;
        const label = d[labelKey];
        const shortLabel = typeof label === 'string' && label.length > 5 ? label.slice(5) : label;
        return (
          <g key={i}>
            <rect x={x} y={y} width={barW} height={barH} fill={color} rx="3" opacity="0.85" />
            <title>{label}: {val}</title>
            <text x={x + barW / 2} y={height + 14} fontSize="9" textAnchor="middle" fill="#65746d">
              {shortLabel}
            </text>
            {val > 0 && (
              <text x={x + barW / 2} y={y - 3} fontSize="9" textAnchor="middle" fill="#172026">
                {val}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ─── Login Page ───────────────────────────────────────────────────────────────

function LoginPage({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const res = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      setToken(res.token);
      setStoredUser(res.user);
      onLogin(res.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="loginPage">
      <form className="loginCard" onSubmit={submit}>
        <div className="loginBrand">
          <ShieldCheck size={36} />
          <h1>Attendance Portal</h1>
          <p>Sign in to your account</p>
        </div>
        <ErrorBanner msg={error} />
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="admin@example.com"
            required
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
          />
        </label>
        <button type="submit" className="primaryButton" disabled={loading}>
          {loading ? <Spinner /> : <LogIn size={16} />}
          {loading ? 'Signing in…' : 'Sign In'}
        </button>
        <p className="loginHint">Default: admin@example.com / admin123</p>
      </form>
    </div>
  );
}

// ─── Company Switcher (platform_admin) ───────────────────────────────────────

function CompanySwitcher({ currentCompany, onSwitch }) {
  const [companies, setCompanies] = useState([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api('/api/companies')
      .then((rows) => {
        setCompanies(rows);
        if (!currentCompany && rows.length > 0) {
          onSwitch(rows[0].id);
        }
      })
      .catch(() => {});
  }, [currentCompany, onSwitch]);

  const current = companies.find((c) => c.id === currentCompany);

  return (
    <div className="companySwitcher">
      <button className="companySelector" onClick={() => setOpen((o) => !o)}>
        <Building2 size={14} />
        <span>{current?.name || 'Select company'}</span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="companyDropdown">
          {companies.map((c) => (
            <button key={c.id} onClick={() => { onSwitch(c.id); setOpen(false); }}>
              {c.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Overview Page ────────────────────────────────────────────────────────────

function OverviewPage({ companyCtx }) {
  const [daily, setDaily] = useState(null);
  const [weekly, setWeekly] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [d, w] = await Promise.all([
        api(`/api/analytics/daily?day=${todayISO()}`, {}, companyCtx),
        api(`/api/analytics/weekly?start_date=${weekStartISO()}`, {}, companyCtx),
      ]);
      setDaily(d);
      setWeekly(w);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [companyCtx]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <>
      <div className="pageHeader">
        <div>
          <h1>Overview</h1>
          <p>{new Date().toLocaleDateString(undefined, { dateStyle: 'full' })}</p>
        </div>
        <button className="iconButton" onClick={load} title="Refresh">
          {loading ? <Spinner /> : <RefreshCw size={18} />}
        </button>
      </div>
      <ErrorBanner msg={error} />

      {daily && (
        <>
          <div className="statsGrid">
            <Metric label="Present Today" value={daily.present} icon={<UserCheck size={18} />} color="#176b4b" />
            <Metric label="Absent Today" value={daily.absent} icon={<Users size={18} />} color="#7b5400" />
            <Metric label="Inside Now" value={daily.inside} icon={<LogIn size={18} />} color="#176b4b" />
            <Metric label="Completed" value={daily.completed} icon={<LogOut size={18} />} />
            <Metric label="Missing Clock-Out" value={daily.missing_clock_out} icon={<AlertTriangle size={18} />} color="#8b2f1f" />
            <Metric label="Pending Approvals" value={daily.pending_approval} icon={<Clock size={18} />} color="#7b5400" />
          </div>
        </>
      )}

      {weekly && (
        <div className="card" style={{ marginTop: 18 }}>
          <h2 style={{ marginBottom: 16 }}><BarChart2 size={18} /> Weekly Attendance Trend</h2>
          <BarChart data={weekly.trend} valueKey="present" labelKey="date" />
          <div className="weeklyStats">
            <div><strong>{weekly.total_hours}h</strong><span>Total Hours</span></div>
            <div><strong>{weekly.avg_hours_per_day}h</strong><span>Avg / Day</span></div>
            <div><strong>{weekly.late_arrivals}</strong><span>Late Arrivals</span></div>
            <div><strong>{weekly.missing_clock_outs}</strong><span>Missing Clock-Outs</span></div>
            <div><strong>{weekly.pending_approvals}</strong><span>Pending Approvals</span></div>
          </div>
        </div>
      )}
    </>
  );
}

// ─── Attendance Page ──────────────────────────────────────────────────────────

function AttendancePage({ companyCtx }) {
  const [data, setData] = useState({ rows: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [startDate, setStartDate] = useState(todayISO());
  const [endDate, setEndDate] = useState(todayISO());

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await api(
        `/api/attendance?start_date=${startDate}&day=${endDate}`,
        {},
        companyCtx,
      );
      setData(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate, companyCtx]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return data.rows;
    return data.rows.filter((r) =>
      [r.name, r.employee_code, r.department, r.role]
        .filter(Boolean)
        .some((v) => v.toLowerCase().includes(needle)),
    );
  }, [data.rows, query]);

  return (
    <>
      <div className="pageHeader">
        <div>
          <h1>Attendance</h1>
          <p>{filtered.length} records</p>
        </div>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />

      <div className="toolbar split" style={{ height: 'auto', padding: '10px 14px', flexWrap: 'wrap', gap: 10 }}>
        <div className="searchBox" style={{ minWidth: 180 }}>
          <Search size={18} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search employee…" />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={{ flexDirection: 'row', gap: 6, alignItems: 'center', fontSize: 13, color: '#65746d' }}>
            From <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 8px' }} />
          </label>
          <label style={{ flexDirection: 'row', gap: 6, alignItems: 'center', fontSize: 13, color: '#65746d' }}>
            To <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 8px' }} />
          </label>
        </div>
      </div>

      <div className="tablePanel">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Department</th>
              <th>Date</th>
              <th>Clock In</th>
              <th>Clock Out</th>
              <th>Duration</th>
              <th>Status</th>
              <th>Confidence</th>
              <th>Approval</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row, i) => (
              <tr key={`${row.employee_id}-${i}`}>
                <td>
                  <strong>{row.name}</strong>
                  <span>{row.employee_code || row.gallery_label}</span>
                </td>
                <td>{row.department || '-'}</td>
                <td>{row.attendance_date ? fmtDate(row.attendance_date) : '-'}</td>
                <td>{fmtTime(row.in_time)}</td>
                <td>{fmtTime(row.out_time)}</td>
                <td>{row.duration_hhmm || '-'}</td>
                <td><StatusBadge status={row.status} inTime={row.in_time} /></td>
                <td>{row.in_confidence != null ? fmtPct(row.in_confidence) : '-'}</td>
                <td><ApprovalBadge status={row.in_approval_status} /></td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan="9" style={{ textAlign: 'center', color: '#65746d' }}>No records found</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ─── Approvals Page ───────────────────────────────────────────────────────────

function ApprovalsPage({ companyCtx }) {
  const [events, setEvents] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionMsg, setActionMsg] = useState('');
  const [correcting, setCorrecting] = useState(null);
  const [correctEmpId, setCorrectEmpId] = useState('');
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [evs, emps] = await Promise.all([
        api('/api/approvals', {}, companyCtx),
        api('/api/employees', {}, companyCtx),
      ]);
      setEvents(evs);
      setEmployees(emps.filter ? emps.filter((e) => e.active) : emps);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [companyCtx]);

  useEffect(() => { load(); }, [load]);

  async function doApprove(eventId, correctedId = null, approveNote = null) {
    setActionMsg('');
    try {
      await api(`/api/approvals/${eventId}/approve`, {
        method: 'POST',
        body: JSON.stringify({ corrected_employee_id: correctedId || null, note: approveNote }),
      }, companyCtx);
      setActionMsg('Event approved.');
      setCorrecting(null);
      await load();
    } catch (err) {
      setActionMsg(err.message);
    }
  }

  async function doReject(eventId) {
    setActionMsg('');
    try {
      await api(`/api/approvals/${eventId}/reject`, {
        method: 'POST',
        body: JSON.stringify({ note: null }),
      }, companyCtx);
      setActionMsg('Event rejected.');
      await load();
    } catch (err) {
      setActionMsg(err.message);
    }
  }

  return (
    <>
      <div className="pageHeader">
        <div>
          <h1>Approvals</h1>
          <p>{events.length} pending</p>
        </div>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />
      {actionMsg && <div className="successBanner">{actionMsg}</div>}

      {events.length === 0 && !loading && (
        <div className="emptyState">
          <CheckCircle size={40} />
          <p>No pending approvals</p>
        </div>
      )}

      <div className="approvalGrid">
        {events.map((ev) => (
          <div className="approvalCard" key={ev.id}>
            <div className="approvalSnap">
              {ev.snapshot_url ? (
                <img src={`${API_BASE}${ev.snapshot_url}`} alt="" />
              ) : (
                <div className="missingImage">No image</div>
              )}
            </div>
            <div className="approvalInfo">
              <strong>{ev.employee_name || 'Unknown'}</strong>
              <span>{ev.employee_code}</span>
              <span>{ev.camera_name} · {ev.direction?.toUpperCase()}</span>
              <span>{fmtTime(ev.event_time)}</span>
              <span>Confidence: {fmtPct(ev.confidence)}</span>
              <span className="reason">{ev.verification_reason || ev.event_type}</span>
            </div>
            <div className="approvalActions">
              {correcting === ev.id ? (
                <>
                  <select
                    value={correctEmpId}
                    onChange={(e) => setCorrectEmpId(e.target.value)}
                    className="selectInput"
                  >
                    <option value="">-- Select employee --</option>
                    {employees.map((e) => (
                      <option key={e.id} value={e.id}>{e.name} ({e.employee_code})</option>
                    ))}
                  </select>
                  <input
                    className="cellInput"
                    placeholder="Note (optional)"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <button
                    className="approveBtn"
                    onClick={() => doApprove(ev.id, correctEmpId ? parseInt(correctEmpId) : null, note || null)}
                    disabled={!correctEmpId}
                  >
                    <CheckCircle size={14} /> Confirm
                  </button>
                  <button className="rejectBtn" onClick={() => setCorrecting(null)}>
                    <X size={14} /> Cancel
                  </button>
                </>
              ) : (
                <>
                  <button className="approveBtn" onClick={() => doApprove(ev.id)}>
                    <CheckCircle size={14} /> Approve
                  </button>
                  <button className="correctBtn" onClick={() => { setCorrecting(ev.id); setCorrectEmpId(''); setNote(''); }}>
                    <Pencil size={14} /> Correct
                  </button>
                  <button className="rejectBtn" onClick={() => doReject(ev.id)}>
                    <XCircle size={14} /> Reject
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

// ─── Employees Page ───────────────────────────────────────────────────────────

function EmployeesPage({ companyCtx, readOnly = false }) {
  const [employees, setEmployees] = useState([]);
  const [form, setForm] = useState({ employee_code: '', name: '', department: '', role: '', gallery_label: '' });
  const [imageFile, setImageFile] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setEmployees(await api('/api/employees', {}, companyCtx));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [companyCtx]);

  useEffect(() => { load(); }, [load]);

  function updateForm(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      const created = await api('/api/employees', {
        method: 'POST',
        body: JSON.stringify(form),
      }, companyCtx);
      if (imageFile) {
        const fd = new FormData();
        fd.append('file', imageFile);
        await api(`/api/employees/${created.id}/image`, { method: 'POST', body: fd }, companyCtx);
      }
      setForm({ employee_code: '', name: '', department: '', role: '', gallery_label: '' });
      setImageFile(null);
      setMessage(imageFile ? 'Employee added. Restart worker to rebuild gallery.' : 'Employee added.');
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(id) {
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/employees/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(editForm),
      }, companyCtx);
      setEditingId(null);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function deleteEmp(id) {
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/employees/${id}`, { method: 'DELETE' }, companyCtx);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function uploadImage(id, file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/employees/${id}/image`, { method: 'POST', body: fd }, companyCtx);
      setMessage('Image uploaded. Restart worker to rebuild gallery.');
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="pageHeader">
        <h1>Employees</h1>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />

      <div className={readOnly ? '' : 'employeesGrid'}>
        {!readOnly && (
          <form className="card employeeForm" onSubmit={submit}>
            <h2><BadgePlus size={18} /> Add Employee</h2>
            {[
              ['Employee ID', 'employee_code', true],
              ['Name', 'name', true],
              ['Department', 'department', false],
              ['Role/Designation', 'role', false],
              ['Gallery Label', 'gallery_label', true],
            ].map(([lbl, field, req]) => (
              <label key={field}>
                {lbl}
                <input
                  value={form[field]}
                  onChange={(e) => updateForm(field, e.target.value)}
                  required={req}
                />
              </label>
            ))}
            <label>
              Face Image
              <input type="file" accept="image/png,image/jpeg" onChange={(e) => setImageFile(e.target.files?.[0] || null)} />
            </label>
            <button type="submit" className="primaryButton" disabled={saving}>
              {saving ? <Spinner /> : <BadgePlus size={16} />} Add Employee
            </button>
            {message && <p className="formMessage">{message}</p>}
          </form>
        )}

        <div className="tablePanel">
          <table>
            <thead>
              <tr>
                <th>Employee</th>
                <th>Department</th>
                <th>Designation</th>
                <th>Gallery Label</th>
                <th>Status</th>
                {!readOnly && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {employees.map((row) => (
                <tr key={row.id} style={!row.active ? { opacity: 0.5 } : {}}>
                  {editingId === row.id ? (
                    <>
                      <td>
                        <input className="cellInput" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} placeholder="Name" />
                        <input className="cellInput small" value={editForm.employee_code} onChange={(e) => setEditForm({ ...editForm, employee_code: e.target.value })} placeholder="ID" />
                      </td>
                      <td><input className="cellInput" value={editForm.department || ''} onChange={(e) => setEditForm({ ...editForm, department: e.target.value })} /></td>
                      <td><input className="cellInput" value={editForm.role || ''} onChange={(e) => setEditForm({ ...editForm, role: e.target.value })} /></td>
                      <td><input className="cellInput" value={editForm.gallery_label} onChange={(e) => setEditForm({ ...editForm, gallery_label: e.target.value })} /></td>
                      <td>
                        <label style={{ flexDirection: 'row', gap: 6, fontSize: 13 }}>
                          <input type="checkbox" checked={editForm.active} onChange={(e) => setEditForm({ ...editForm, active: e.target.checked })} /> Active
                        </label>
                      </td>
                      <td className="actions">
                        <button title="Save" onClick={() => saveEdit(row.id)} disabled={saving}><Save size={16} /></button>
                        <button title="Cancel" onClick={() => setEditingId(null)}><X size={16} /></button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td><strong>{row.name}</strong><span>{row.employee_code}</span></td>
                      <td>{row.department || '-'}</td>
                      <td>{row.role || '-'}</td>
                      <td>{row.gallery_label}</td>
                      <td><span className={`status ${row.active ? 'inside' : 'outside'}`}>{row.active ? 'Active' : 'Inactive'}</span></td>
                      {!readOnly && (
                        <td className="actions">
                          <label className="uploadButton" title="Upload face image">
                            <Camera size={16} />
                            <input type="file" accept="image/png,image/jpeg" onChange={(e) => uploadImage(row.id, e.target.files?.[0])} />
                          </label>
                          <button title="Edit" onClick={() => { setEditingId(row.id); setEditForm({ ...row }); }}><Pencil size={16} /></button>
                          <ConfirmButton
                            className="actions"
                            confirmMsg="Deactivate this employee?"
                            onConfirm={() => deleteEmp(row.id)}
                          >
                            <button title="Deactivate"><Trash2 size={16} /></button>
                          </ConfirmButton>
                        </td>
                      )}
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ─── Users Page ───────────────────────────────────────────────────────────────

const ROLES = ['company_admin', 'hr', 'manager', 'employee'];
const ALL_ROLES = ['platform_admin', ...ROLES];

function UsersPage({ companyCtx }) {
  const { user } = useAuth();
  const [users, setUsers] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [form, setForm] = useState({ email: '', password: '', role: 'employee', employee_id: '' });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [u, e] = await Promise.all([
        api('/api/users', {}, companyCtx),
        api('/api/employees', {}, companyCtx),
      ]);
      setUsers(u);
      setEmployees(e.filter ? e.filter((emp) => emp.active) : e);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [companyCtx]);

  useEffect(() => { load(); }, [load]);

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      const payload = { ...form, employee_id: form.employee_id ? parseInt(form.employee_id) : null };
      await api('/api/users', { method: 'POST', body: JSON.stringify(payload) }, companyCtx);
      setForm({ email: '', password: '', role: 'employee', employee_id: '' });
      setMessage('User created.');
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(id) {
    setSaving(true);
    setMessage('');
    try {
      const payload = { ...editForm };
      if (payload.employee_id === '') payload.employee_id = null;
      if (!payload.password) delete payload.password;
      await api(`/api/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }, companyCtx);
      setEditingId(null);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function deactivate(id) {
    setSaving(true);
    try {
      await api(`/api/users/${id}`, { method: 'DELETE' }, companyCtx);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  const availableRoles = user?.role === 'platform_admin' ? ALL_ROLES : ROLES;

  return (
    <>
      <div className="pageHeader">
        <h1>Users &amp; Roles</h1>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />

      <div className="employeesGrid">
        <form className="card employeeForm" onSubmit={submit}>
          <h2><UserCheck size={18} /> Add User</h2>
          <label>
            Email
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          </label>
          <label>
            Password
            <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={6} />
          </label>
          <label>
            Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className="selectInput">
              {availableRoles.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label>
            Link Employee (optional)
            <select value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })} className="selectInput">
              <option value="">-- None --</option>
              {employees.map((e) => <option key={e.id} value={e.id}>{e.name} ({e.employee_code})</option>)}
            </select>
          </label>
          <button type="submit" className="primaryButton" disabled={saving}>
            {saving ? <Spinner /> : <BadgePlus size={16} />} Add User
          </button>
          {message && <p className="formMessage">{message}</p>}
        </form>

        <div className="tablePanel">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Linked Employee</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((row) => (
                <tr key={row.id} style={!row.active ? { opacity: 0.5 } : {}}>
                  {editingId === row.id ? (
                    <>
                      <td><input className="cellInput" value={editForm.email} onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} /></td>
                      <td>
                        <select value={editForm.role} onChange={(e) => setEditForm({ ...editForm, role: e.target.value })} className="selectInput">
                          {availableRoles.map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      </td>
                      <td>
                        <select value={editForm.employee_id || ''} onChange={(e) => setEditForm({ ...editForm, employee_id: e.target.value })} className="selectInput">
                          <option value="">-- None --</option>
                          {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                        </select>
                      </td>
                      <td>
                        <label style={{ flexDirection: 'row', gap: 6, fontSize: 13 }}>
                          <input type="checkbox" checked={editForm.active} onChange={(e) => setEditForm({ ...editForm, active: e.target.checked })} /> Active
                        </label>
                      </td>
                      <td className="actions">
                        <button onClick={() => saveEdit(row.id)} disabled={saving}><Save size={16} /></button>
                        <button onClick={() => setEditingId(null)}><X size={16} /></button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td><strong>{row.email}</strong></td>
                      <td><span className="roleBadge">{row.role}</span></td>
                      <td>{row.employee_name || '-'}</td>
                      <td><span className={`status ${row.active ? 'inside' : 'outside'}`}>{row.active ? 'Active' : 'Disabled'}</span></td>
                      <td className="actions">
                        <button title="Edit" onClick={() => { setEditingId(row.id); setEditForm({ ...row, password: '' }); }}><Pencil size={16} /></button>
                        <button title="Disable" onClick={() => deactivate(row.id)}><Trash2 size={16} /></button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ─── Cameras Page ─────────────────────────────────────────────────────────────

function CamerasPage({ companyCtx }) {
  const [cameras, setCameras] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setCameras(await api('/api/cameras', {}, companyCtx));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [companyCtx]);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <>
      <div className="pageHeader">
        <h1>Cameras</h1>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />
      <div className="tablePanel">
        <table>
          <thead>
            <tr>
              <th>Camera</th>
              <th>Direction</th>
              <th>Status</th>
              <th>FPS</th>
              <th>Inference</th>
              <th>Queue</th>
              <th>Reconnects</th>
              <th>Last Frame</th>
            </tr>
          </thead>
          <tbody>
            {cameras.map((c) => (
              <tr key={c.id}>
                <td><strong>{c.name}</strong><span>{c.stream_url || '-'}</span></td>
                <td>{c.direction === 'in' ? 'Entry' : 'Exit'}</td>
                <td><StatusBadge status={c.status || 'unknown'} /></td>
                <td>{c.fps == null ? '-' : c.fps.toFixed(1)}</td>
                <td>{c.inference_ms == null ? '-' : `${Math.round(c.inference_ms)} ms`}</td>
                <td>{c.api_queue_size ?? '-'}</td>
                <td>{c.reconnect_count ?? 0}</td>
                <td>{fmtTime(c.last_frame_at)}</td>
              </tr>
            ))}
            {cameras.length === 0 && (
              <tr><td colSpan="8" style={{ textAlign: 'center', color: '#65746d' }}>No cameras reported yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ─── My Attendance Page ───────────────────────────────────────────────────────

function MyAttendancePage() {
  const [data, setData] = useState({ sessions: [], pending_events: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return isoDate(d);
  });
  const [endDate, setEndDate] = useState(todayISO());

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await api(`/api/attendance/me?start_date=${startDate}&end_date=${endDate}`));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => { load(); }, [load]);

  const totalHours = useMemo(() => {
    const secs = data.sessions.reduce((sum, s) => sum + (s.duration_seconds || 0), 0);
    return (secs / 3600).toFixed(1);
  }, [data.sessions]);

  return (
    <>
      <div className="pageHeader">
        <div>
          <h1>My Attendance</h1>
          <p>Your personal attendance record</p>
        </div>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ flexDirection: 'row', gap: 6, alignItems: 'center', fontSize: 13, color: '#65746d' }}>
          From <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 8px' }} />
        </label>
        <label style={{ flexDirection: 'row', gap: 6, alignItems: 'center', fontSize: 13, color: '#65746d' }}>
          To <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 8px' }} />
        </label>
      </div>

      <div className="statsGrid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: 18 }}>
        <Metric label="Days Present" value={data.sessions.filter((s) => s.in_time).length} icon={<UserCheck size={18} />} color="#176b4b" />
        <Metric label="Total Hours" value={totalHours} icon={<Clock size={18} />} />
        <Metric label="Pending Events" value={data.pending_events.length} icon={<AlertTriangle size={18} />} color="#7b5400" />
      </div>

      <div className="tablePanel">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Clock In</th>
              <th>Clock Out</th>
              <th>Duration</th>
              <th>Status</th>
              <th>Approval</th>
            </tr>
          </thead>
          <tbody>
            {data.sessions.map((s, i) => (
              <tr key={i}>
                <td>{fmtDate(s.attendance_date)}</td>
                <td>{fmtTime(s.in_time)}</td>
                <td>{fmtTime(s.out_time)}</td>
                <td>{s.duration_hhmm || '-'}</td>
                <td><StatusBadge status={s.status} inTime={s.in_time} /></td>
                <td><ApprovalBadge status={s.in_approval_status} /></td>
              </tr>
            ))}
            {data.sessions.length === 0 && !loading && (
              <tr><td colSpan="6" style={{ textAlign: 'center', color: '#65746d' }}>No sessions found for this period.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {data.pending_events.length > 0 && (
        <div className="card" style={{ marginTop: 18 }}>
          <h2 style={{ marginBottom: 12 }}><AlertTriangle size={18} /> Pending Events</h2>
          {data.pending_events.map((ev) => (
            <div key={ev.id} className="pendingEventRow">
              <span>{ev.direction?.toUpperCase()}</span>
              <span>{fmtTime(ev.event_time)}</span>
              <span>Confidence: {fmtPct(ev.confidence)}</span>
              <ApprovalBadge status={ev.approval_status} />
              <span className="reason">{ev.verification_reason}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ─── Analytics Page ───────────────────────────────────────────────────────────

function AnalyticsPage({ companyCtx }) {
  const [daily, setDaily] = useState(null);
  const [weekly, setWeekly] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [day, setDay] = useState(todayISO());
  const [weekStart, setWeekStart] = useState(weekStartISO());
  const [dept, setDept] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const deptParam = dept ? `&department=${encodeURIComponent(dept)}` : '';
    try {
      const [d, w] = await Promise.all([
        api(`/api/analytics/daily?day=${day}${deptParam}`, {}, companyCtx),
        api(`/api/analytics/weekly?start_date=${weekStart}${deptParam}`, {}, companyCtx),
      ]);
      setDaily(d);
      setWeekly(w);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [day, weekStart, dept, companyCtx]);

  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="pageHeader">
        <h1>Analytics</h1>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />

      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ flexDirection: 'row', gap: 6, alignItems: 'center', fontSize: 13, color: '#65746d' }}>
          Day <input type="date" value={day} onChange={(e) => setDay(e.target.value)} style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 8px' }} />
        </label>
        <label style={{ flexDirection: 'row', gap: 6, alignItems: 'center', fontSize: 13, color: '#65746d' }}>
          Week <input type="date" value={weekStart} onChange={(e) => setWeekStart(e.target.value)} style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 8px' }} />
        </label>
        <input
          placeholder="Filter by department…"
          value={dept}
          onChange={(e) => setDept(e.target.value)}
          style={{ height: 32, border: '1px solid #cfdad4', borderRadius: 6, padding: '0 10px', fontSize: 13 }}
        />
      </div>

      {daily && (
        <div className="card" style={{ marginBottom: 18 }}>
          <h2 style={{ marginBottom: 14 }}>Daily — {fmtDate(daily.date)}</h2>
          <div className="statsGrid">
            <Metric label="Present" value={daily.present} icon={<UserCheck size={18} />} color="#176b4b" />
            <Metric label="Absent" value={daily.absent} icon={<Users size={18} />} color="#7b5400" />
            <Metric label="Inside" value={daily.inside} icon={<LogIn size={18} />} />
            <Metric label="Completed" value={daily.completed} icon={<LogOut size={18} />} />
            <Metric label="Missing Clock-Out" value={daily.missing_clock_out} icon={<AlertTriangle size={18} />} color="#8b2f1f" />
            <Metric label="Pending Approval" value={daily.pending_approval} icon={<Clock size={18} />} color="#7b5400" />
          </div>
        </div>
      )}

      {weekly && (
        <div className="card">
          <h2 style={{ marginBottom: 14 }}>Weekly Trend — {fmtDate(weekly.week_start)} to {fmtDate(weekly.week_end)}</h2>
          <BarChart data={weekly.trend} valueKey="present" labelKey="date" />
          <div className="weeklyStats">
            <div><strong>{weekly.total_hours}h</strong><span>Total Hours</span></div>
            <div><strong>{weekly.avg_hours_per_day}h</strong><span>Avg / Day</span></div>
            <div><strong>{weekly.late_arrivals}</strong><span>Late Arrivals</span></div>
            <div><strong>{weekly.missing_clock_outs}</strong><span>Missing Clock-Outs</span></div>
            <div><strong>{weekly.pending_approvals}</strong><span>Pending Approvals</span></div>
          </div>

          <div className="tablePanel" style={{ marginTop: 18 }}>
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Present</th>
                  <th>Inside</th>
                  <th>Completed</th>
                  <th>Missing Clock-Out</th>
                  <th>Total Hours</th>
                </tr>
              </thead>
              <tbody>
                {weekly.trend.map((d) => (
                  <tr key={d.date}>
                    <td>{fmtDate(d.date)}</td>
                    <td>{d.present}</td>
                    <td>{d.inside}</td>
                    <td>{d.completed}</td>
                    <td>{d.missing_clock_out}</td>
                    <td>{d.total_hours}h</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

// ─── Companies Page (platform_admin) ─────────────────────────────────────────

function CompaniesPage() {
  const [companies, setCompanies] = useState([]);
  const [form, setForm] = useState({ name: '', slug: '' });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setCompanies(await api('/api/companies'));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      await api('/api/companies', { method: 'POST', body: JSON.stringify(form) });
      setForm({ name: '', slug: '' });
      setMessage('Company created.');
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(id) {
    setSaving(true);
    setMessage('');
    try {
      await api(`/api/companies/${id}`, { method: 'PATCH', body: JSON.stringify(editForm) });
      setEditingId(null);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="pageHeader">
        <h1>Companies</h1>
        <button className="iconButton" onClick={load}>{loading ? <Spinner /> : <RefreshCw size={18} />}</button>
      </div>
      <ErrorBanner msg={error} />

      <div className="employeesGrid">
        <form className="card employeeForm" onSubmit={submit}>
          <h2><Building2 size={18} /> Add Company</h2>
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
          <label>Slug (URL-safe)<input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} required /></label>
          <button type="submit" className="primaryButton" disabled={saving}>
            {saving ? <Spinner /> : <BadgePlus size={16} />} Add Company
          </button>
          {message && <p className="formMessage">{message}</p>}
        </form>

        <div className="tablePanel">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Slug</th>
                <th>Status</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => (
                <tr key={c.id}>
                  {editingId === c.id ? (
                    <>
                      <td><input className="cellInput" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} /></td>
                      <td><input className="cellInput" value={editForm.slug} onChange={(e) => setEditForm({ ...editForm, slug: e.target.value })} /></td>
                      <td>
                        <label style={{ flexDirection: 'row', gap: 6, fontSize: 13 }}>
                          <input type="checkbox" checked={editForm.active} onChange={(e) => setEditForm({ ...editForm, active: e.target.checked })} /> Active
                        </label>
                      </td>
                      <td>{fmtDate(c.created_at)}</td>
                      <td className="actions">
                        <button onClick={() => saveEdit(c.id)} disabled={saving}><Save size={16} /></button>
                        <button onClick={() => setEditingId(null)}><X size={16} /></button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td><strong>{c.name}</strong></td>
                      <td>{c.slug}</td>
                      <td><span className={`status ${c.active ? 'inside' : 'outside'}`}>{c.active ? 'Active' : 'Inactive'}</span></td>
                      <td>{fmtDate(c.created_at)}</td>
                      <td className="actions">
                        <button onClick={() => { setEditingId(c.id); setEditForm({ ...c }); }}><Pencil size={16} /></button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ─── Navigation Config ────────────────────────────────────────────────────────

function navItems(role) {
  const all = [
    { key: 'overview', label: 'Overview', icon: <BarChart2 size={18} />, roles: ['platform_admin', 'company_admin', 'hr', 'manager'] },
    { key: 'attendance', label: 'Attendance', icon: <Clock size={18} />, roles: ['platform_admin', 'company_admin', 'hr', 'manager'] },
    { key: 'approvals', label: 'Approvals', icon: <CheckCircle size={18} />, roles: ['platform_admin', 'company_admin', 'hr'] },
    { key: 'employees', label: 'Employees', icon: <Users size={18} />, roles: ['platform_admin', 'company_admin', 'hr', 'manager'] },
    { key: 'users', label: 'Users & Roles', icon: <UserCheck size={18} />, roles: ['platform_admin', 'company_admin'] },
    { key: 'cameras', label: 'Cameras', icon: <Camera size={18} />, roles: ['platform_admin', 'company_admin', 'hr', 'manager'] },
    { key: 'analytics', label: 'Analytics', icon: <BarChart2 size={18} />, roles: ['platform_admin', 'company_admin', 'hr', 'manager'] },
    { key: 'my-attendance', label: 'My Attendance', icon: <LogIn size={18} />, roles: ['employee'] },
    { key: 'companies', label: 'Companies', icon: <Building2 size={18} />, roles: ['platform_admin'] },
  ];
  return all.filter((item) => item.roles.includes(role));
}

function defaultTab(role) {
  if (role === 'employee') return 'my-attendance';
  return 'overview';
}

// ─── Main App ─────────────────────────────────────────────────────────────────

function App() {
  const [authUser, setAuthUser] = useState(() => getStoredUser());
  const [tab, setTab] = useState(() => {
    const u = getStoredUser();
    return u ? defaultTab(u.role) : 'overview';
  });
  const [companyCtx, setCompanyCtx] = useState(null);

  useEffect(() => {
    function onExpired() {
      setAuthUser(null);
    }
    window.addEventListener('auth:expired', onExpired);
    return () => window.removeEventListener('auth:expired', onExpired);
  }, []);

  function handleLogin(user) {
    setAuthUser(user);
    setTab(defaultTab(user.role));
  }

  function handleLogout() {
    clearAuth();
    setAuthUser(null);
  }

  if (!authUser) {
    return <LoginPage onLogin={handleLogin} />;
  }

  const items = navItems(authUser.role);
  const effectiveCtx = authUser.role === 'platform_admin' ? companyCtx : authUser.company_id;

  return (
    <AuthCtx.Provider value={{ user: authUser, companyCtx: effectiveCtx }}>
      <div className="app">
        <aside className="sidebar">
          <div className="brand">
            <ShieldCheck size={28} />
            <div>
              <strong>Attendance</strong>
              <span>{authUser.role}</span>
            </div>
          </div>

          {authUser.role === 'platform_admin' && (
            <CompanySwitcher currentCompany={companyCtx} onSwitch={setCompanyCtx} />
          )}

          <nav>
            {items.map((item) => (
              <button
                key={item.key}
                className={tab === item.key ? 'active' : ''}
                onClick={() => setTab(item.key)}
              >
                {item.icon} {item.label}
              </button>
            ))}
          </nav>

          <div className="sidebarFooter">
            <div className="userInfo">
              <Shield size={14} />
              <span>{authUser.email}</span>
            </div>
            <button className="logoutBtn" onClick={handleLogout}>
              <LogOut size={14} /> Sign out
            </button>
          </div>
        </aside>

        <main>
          {tab === 'overview' && <OverviewPage companyCtx={effectiveCtx} />}
          {tab === 'attendance' && <AttendancePage companyCtx={effectiveCtx} />}
          {tab === 'approvals' && <ApprovalsPage companyCtx={effectiveCtx} />}
          {tab === 'employees' && (
            <EmployeesPage
              companyCtx={effectiveCtx}
              readOnly={!['platform_admin', 'company_admin'].includes(authUser.role)}
            />
          )}
          {tab === 'users' && <UsersPage companyCtx={effectiveCtx} />}
          {tab === 'cameras' && <CamerasPage companyCtx={effectiveCtx} />}
          {tab === 'analytics' && <AnalyticsPage companyCtx={effectiveCtx} />}
          {tab === 'my-attendance' && <MyAttendancePage />}
          {tab === 'companies' && <CompaniesPage />}
        </main>
      </div>
    </AuthCtx.Provider>
  );
}

createRoot(document.getElementById('root')).render(<App />);
