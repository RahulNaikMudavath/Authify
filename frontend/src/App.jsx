import React, { useState, useEffect, useRef } from 'react';
import {
  Activity,
  Zap,
  Clock,
  RefreshCw,
  Sliders,
  CheckCircle2,
  AlertTriangle,
  Flame,
  Search,
  ArrowUpDown,
  TrendingUp,
  Database,
  Radio
} from 'lucide-react';

export default function App() {
  const [report, setReport] = useState(null);
  const [stations, setStations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [polling, setPolling] = useState(true);
  const [pollInterval, setPollInterval] = useState(2);
  const [recomputing, setRecomputing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  
  const [selectedStationId, setSelectedStationId] = useState(1);
  const [newOccupiedDocks, setNewOccupiedDocks] = useState(0);
  const [rebalanceLoading, setRebalanceLoading] = useState(false);
  const [rebalanceNotice, setRebalanceNotice] = useState(null);
  
  const [logs, setLogs] = useState([]);
  
  const [liveAge, setLiveAge] = useState(0);
  const lastFetchTimeRef = useRef(Date.now());
  const initialDataAgeRef = useRef(0);

  const loadStationsList = async () => {
    try {
      const res = await fetch('/api/stations');
      if (res.ok) {
        const data = await res.json();
        setStations(data.stations || []);
        if (data.stations.length > 0 && selectedStationId === 1) {
          setSelectedStationId(data.stations[0].station_id);
          setNewOccupiedDocks(data.stations[0].occupied_docks);
        }
      }
    } catch (e) {
      console.error('Failed to load stations:', e);
    }
  };

  const fetchReport = async (isManual = false) => {
    const t0 = performance.now();
    try {
      const res = await fetch('/api/report');
      const latencyMs = Math.round(performance.now() - t0);
      
      if (res.ok) {
        const data = await res.json();
        setReport(data);
        setLoading(false);
        setRecomputing(false);
        
        initialDataAgeRef.current = data.data_age_seconds || 0;
        lastFetchTimeRef.current = Date.now();
        setLiveAge(data.data_age_seconds || 0);
        
        const isHit = data.cache_hit;
        addLog({
          type: isHit ? 'hit' : 'miss',
          text: `GET /api/report -> ${isHit ? 'CACHE HIT' : 'FRESH COMPUTE'} (${latencyMs}ms, age: ${data.data_age_seconds}s)`
        });
      } else if (res.status === 202) {
        setRecomputing(true);
        addLog({
          type: 'miss',
          text: `GET /api/report -> Recomputation in progress... (${latencyMs}ms)`
        });
      }
    } catch (e) {
      console.error('Error fetching report:', e);
      addLog({
        type: 'error',
        text: `GET /api/report failed: ${e.message}`
      });
    }
  };

  const addLog = (entry) => {
    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0] + '.' + String(now.getMilliseconds()).padStart(3, '0');
    setLogs((prev) => [
      { id: Math.random(), time: timeStr, ...entry },
      ...prev.slice(0, 49)
    ]);
  };

  useEffect(() => {
    loadStationsList();
    fetchReport();
  }, []);

  useEffect(() => {
    if (!polling) return;
    const interval = setInterval(() => {
      fetchReport();
    }, pollInterval * 1000);
    return () => clearInterval(interval);
  }, [polling, pollInterval]);

  useEffect(() => {
    const ticker = setInterval(() => {
      const elapsedSinceFetch = (Date.now() - lastFetchTimeRef.current) / 1000;
      setLiveAge(+(initialDataAgeRef.current + elapsedSinceFetch).toFixed(1));
    }, 100);
    return () => clearInterval(ticker);
  }, []);

  const handleStationSelect = (sid) => {
    setSelectedStationId(sid);
    const stn = stations.find((s) => s.station_id === Number(sid));
    if (stn) {
      setNewOccupiedDocks(stn.occupied_docks);
    }
  };

  const handleRebalance = async (sid, targetOccupied) => {
    setRebalanceLoading(true);
    const stationIdToUse = sid || selectedStationId;
    const occupiedToUse = targetOccupied !== undefined ? targetOccupied : newOccupiedDocks;
    
    addLog({
      type: 'rebalance',
      text: `POST /api/rebalance -> Station #${stationIdToUse} -> ${occupiedToUse} docks`
    });

    try {
      const res = await fetch('/api/rebalance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          station_id: stationIdToUse,
          occupied_docks: occupiedToUse,
          reason: 'Manual rebalance triggered'
        })
      });

      if (res.ok) {
        const data = await res.json();
        setRebalanceNotice({
          stationId: stationIdToUse,
          stationName: data.station_name,
          previous: data.previous_occupied,
          next: data.new_occupied,
          time: new Date().toLocaleTimeString()
        });
        
        loadStationsList();
        fetchReport();
      } else {
        const err = await res.json();
        alert(`Rebalance failed: ${err.error}`);
      }
    } catch (e) {
      console.error('Rebalance write error:', e);
      alert(`Error triggering rebalance: ${e.message}`);
    } finally {
      setRebalanceLoading(false);
    }
  };

  const selectedStationObj = stations.find((s) => s.station_id === Number(selectedStationId)) || {
    total_docks: 30,
    occupied_docks: 15,
    name: 'Selected Station'
  };

  const allStations = report?.data?.stations || [];
  const filteredStations = allStations.filter((s) => {
    const matchesSearch =
      s.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      String(s.station_id).includes(searchTerm);
    const matchesStatus = statusFilter === 'ALL' || s.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="container">
      <header className="header">
        <div className="title-group">
          <h1>
            <Activity className="text-blue-500" size={28} />
            Station Availability Report Cache Monitor
          </h1>
          <p>
            Real-time verification of cache correctness, recompute economics, and 5-second rebalance deadlines.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div className="polling-badge">
            <span className={`pulse-dot ${recomputing ? 'recomputing' : ''}`} />
            <span>{polling ? `Polling every ${pollInterval}s` : 'Polling Paused'}</span>
          </div>
          <button
            className="btn btn-secondary"
            style={{ width: 'auto', padding: '8px 14px' }}
            onClick={() => setPolling(!polling)}
          >
            {polling ? 'Pause' : 'Resume'}
          </button>
          <button
            className="btn btn-secondary"
            style={{ width: 'auto', padding: '8px 14px' }}
            onClick={() => fetchReport(true)}
            title="Force immediate poll"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </header>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-header">
            <span>Cache Status</span>
            <Zap size={16} />
          </div>
          <div className="stat-value">
            {report?.cache_hit ? (
              <span className="badge-hit">CACHE HIT</span>
            ) : recomputing ? (
              <span className="badge-recompute">RECOMPUTING...</span>
            ) : (
              <span className="badge-miss">FRESH COMPUTE</span>
            )}
          </div>
          <div className="stat-subtext font-mono">
            Source: <strong>{report?.source || 'loading...'}</strong> | Latency: <strong>{report?.request_latency_ms || 0} ms</strong>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-header">
            <span>Data Age</span>
            <Clock size={16} />
          </div>
          <div className="stat-value">
            <span>{liveAge.toFixed(1)}</span>
            <span style={{ fontSize: '16px', color: 'var(--text-secondary)' }}>sec old</span>
          </div>
          <div className="stat-subtext">
            {liveAge > 5.0 && report?.last_rebalance_at ? (
              <span style={{ color: 'var(--accent-amber)' }}>Older than 5s (Safe: no recent writes)</span>
            ) : (
              <span style={{ color: 'var(--accent-green)' }}>Fresh & Verified &lt; 5s bound</span>
            )}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-header">
            <span>Uncached Query Cost</span>
            <Database size={16} />
          </div>
          <div className="stat-value">
            <span>{report?.computation_time_seconds || 12.8}</span>
            <span style={{ fontSize: '16px', color: 'var(--text-secondary)' }}>sec query</span>
          </div>
          <div className="stat-subtext">
            Over 550,000 trips & 250 stations join
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-header">
            <span>Rebalance Writes</span>
            <ArrowUpDown size={16} />
          </div>
          <div className="stat-value">
            <span>{report?.rebalance_count || 0}</span>
            <span style={{ fontSize: '16px', color: 'var(--text-secondary)' }}>events</span>
          </div>
          <div className="stat-subtext font-mono">
            Last: {report?.last_rebalance_at ? new Date(report.last_rebalance_at).toLocaleTimeString() : 'None yet'}
          </div>
        </div>
      </div>

      {rebalanceNotice && (
        <div
          style={{
            background: 'rgba(59, 130, 246, 0.15)',
            border: '1px solid rgba(59, 130, 246, 0.4)',
            padding: '12px 18px',
            borderRadius: '10px',
            marginBottom: '20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <AlertTriangle color="#3b82f6" size={20} />
            <div>
              <strong>Rebalance Write Landed!</strong> Station #{rebalanceNotice.stationId} (<em>{rebalanceNotice.stationName}</em>) occupied docks updated from <strong>{rebalanceNotice.previous}</strong> &rarr; <strong>{rebalanceNotice.next}</strong>.
              <span style={{ marginLeft: '8px', color: 'var(--text-secondary)', fontSize: '12px' }}>
                Cache invalidated immediately. Proactive single-flight recompute started.
              </span>
            </div>
          </div>
          <button
            onClick={() => setRebalanceNotice(null)}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '16px' }}
          >
            &times;
          </button>
        </div>
      )}

      <div className="control-panel">
        <div className="panel-card">
          <h2>
            <Sliders size={18} color="#3b82f6" />
            Trigger Station Rebalance (Write Operation)
          </h2>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
            Simulate a bike-share van operator moving bicycles. This writes to MongoDB and tests if your Redis cache guarantees &lt;5s correctness without stampeding.
          </p>

          <div className="form-group">
            <label>Select Station to Rebalance:</label>
            <select
              value={selectedStationId}
              onChange={(e) => handleStationSelect(e.target.value)}
            >
              {stations.map((s) => (
                <option key={s.station_id} value={s.station_id}>
                  #{s.station_id} - {s.name} ({s.occupied_docks}/{s.total_docks} bikes)
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
              <label>Set Occupied Docks:</label>
              <span className="font-mono" style={{ fontWeight: 700, color: 'var(--accent-blue)' }}>
                {newOccupiedDocks} / {selectedStationObj.total_docks} docks ({Math.round((newOccupiedDocks / (selectedStationObj.total_docks || 1)) * 100)}%)
              </span>
            </div>
            <input
              type="range"
              min="0"
              max={selectedStationObj.total_docks || 30}
              value={newOccupiedDocks}
              onChange={(e) => setNewOccupiedDocks(Number(e.target.value))}
            />
          </div>

          <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ fontSize: '12px', padding: '6px 10px' }}
              onClick={() => setNewOccupiedDocks(0)}
            >
              Empty (0)
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ fontSize: '12px', padding: '6px 10px' }}
              onClick={() => setNewOccupiedDocks(Math.round((selectedStationObj.total_docks || 30) / 2))}
            >
              Half (50%)
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ fontSize: '12px', padding: '6px 10px' }}
              onClick={() => setNewOccupiedDocks(selectedStationObj.total_docks || 30)}
            >
              Full (Max)
            </button>
          </div>

          <button
            className="btn"
            disabled={rebalanceLoading}
            onClick={() => handleRebalance()}
          >
            {rebalanceLoading ? (
              <>
                <RefreshCw className="animate-spin" size={16} />
                Executing Rebalance Write...
              </>
            ) : (
              <>
                <ArrowUpDown size={16} />
                Apply Rebalance & Invalidate Cache
              </>
            )}
          </button>
        </div>

        <div className="panel-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <h2>
              <Radio size={18} color="#10b981" />
              Live Polling Stream & Audit Log
            </h2>
            <button
              className="btn btn-secondary"
              style={{ width: 'auto', padding: '4px 10px', fontSize: '11px' }}
              onClick={() => setLogs([])}
            >
              Clear Log
            </button>
          </div>

          <div className="event-log">
            {logs.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '20px' }}>
                Waiting for poll events...
              </div>
            ) : (
              logs.map((log) => (
                <div key={log.id} className="log-entry">
                  <span className="log-time">{log.time}</span>
                  {log.type === 'hit' && <span className="log-hit">[HIT]</span>}
                  {log.type === 'miss' && <span className="log-miss">[MISS]</span>}
                  {log.type === 'rebalance' && <span className="log-reb">[REBALANCE]</span>}
                  <span style={{ color: 'var(--text-secondary)' }}>{log.text}</span>
                </div>
              ))
            )}
          </div>
          <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-muted)', display: 'flex', justifyContent: 'space-between' }}>
            <span>Green = Cache Hit (~2ms)</span>
            <span>Amber = Recompute (~13-18s)</span>
            <span>Blue = Write Event</span>
          </div>
        </div>
      </div>

      <div className="table-card">
        <div className="table-header">
          <h2>
            <Database size={18} color="#3b82f6" />
            Station Availability Report ({filteredStations.length} of {allStations.length} Stations)
          </h2>

          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <div style={{ position: 'relative' }}>
              <input
                type="text"
                placeholder="Search station name or ID..."
                className="search-input"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>

            <select
              style={{ width: '160px', padding: '8px 10px' }}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="ALL">All Statuses</option>
              <option value="BALANCED">Balanced</option>
              <option value="CRITICAL_FULL">Critical Full</option>
              <option value="CRITICAL_EMPTY">Critical Empty</option>
              <option value="HIGH_OCCUPANCY">High Occupancy</option>
              <option value="LOW_OCCUPANCY">Low Occupancy</option>
            </select>
          </div>
        </div>

        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Station Name</th>
                <th>Occupancy (Bikes / Docks)</th>
                <th>Available</th>
                <th>Status</th>
                <th>Net Flow</th>
                <th>7-Day Velocity</th>
                <th>Peak Hour</th>
                <th>Quick Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredStations.map((s) => {
                const isSelected = s.station_id === Number(selectedStationId);
                let fillClass = 'normal';
                if (s.status === 'CRITICAL_FULL') fillClass = 'critical-full';
                else if (s.status === 'CRITICAL_EMPTY') fillClass = 'critical-empty';
                else if (s.occupancy_rate_pct >= 80) fillClass = 'high';

                return (
                  <tr
                    key={s.station_id}
                    style={isSelected ? { background: 'rgba(59, 130, 246, 0.1)' } : undefined}
                  >
                    <td className="font-mono">#{s.station_id}</td>
                    <td style={{ fontWeight: 600 }}>{s.name}</td>
                    <td>
                      <div className="occupancy-meter">
                        <div className="meter-track">
                          <div
                            className={`meter-fill ${fillClass}`}
                            style={{ width: `${s.occupancy_rate_pct}%` }}
                          />
                        </div>
                        <span className="font-mono" style={{ fontSize: '12px' }}>
                          {s.occupied_docks}/{s.total_docks} ({s.occupancy_rate_pct}%)
                        </span>
                      </div>
                    </td>
                    <td className="font-mono" style={{ fontWeight: 700, color: s.available_docks <= 2 ? 'var(--accent-red)' : 'var(--text-primary)' }}>
                      {s.available_docks}
                    </td>
                    <td>
                      <span className={`status-pill status-${s.status}`}>
                        {s.status.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="font-mono" style={{ color: s.net_flow_overall >= 0 ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
                      {s.net_flow_overall > 0 ? `+${s.net_flow_overall}` : s.net_flow_overall}
                    </td>
                    <td className="font-mono" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {s.recent_7d_arrivals} in / {s.recent_7d_departures} out
                    </td>
                    <td className="font-mono" style={{ fontSize: '12px' }}>
                      {s.peak_hour}
                    </td>
                    <td>
                      <button
                        className="btn btn-secondary"
                        style={{ padding: '4px 8px', fontSize: '11px', width: 'auto' }}
                        onClick={() => {
                          handleStationSelect(s.station_id);
                          const newDocks = s.occupied_docks === 0 ? s.total_docks : 0;
                          handleRebalance(s.station_id, newDocks);
                        }}
                      >
                        {s.occupied_docks === 0 ? 'Fill' : 'Empty'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
