"""
App Monitoring - Application Streamlit de monitoring TSDB + SQL
Architecture: modèle de classes VisualRender, VisualRenderGeneral, WebRender, FetchApi
"""

import streamlit as st
import pandas as pd
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Configuration de la page
try:
    st.set_page_config(
        page_title="Monitoring Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )
except:
    pass

# Import des modèles et services
import sys
sys.path.insert(0, '/mnt/agents/output/monitoring_app')

from models.renderers import VisualRender, VisualRenderGeneral, WebRender
from models.fetcher import FetchApi, FetchResponse
from services.tsdb_connectors import (
    InfluxDBConnector, VictoriaMetricsConnector, 
    PrometheusConnector, TSDBManager
)
from services.sql_monitor import SQLMonitor, SQLMonitorManager, SQLMetric

# ---------- CSS Personnalisé ----------
CUSTOM_CSS = """
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #00bc96;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #888;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, rgba(0,188,150,0.1) 0%, rgba(0,0,0,0.2) 100%);
        border: 1px solid rgba(0,188,150,0.3);
        border-radius: 10px;
        padding: 1rem;
    }
    .status-up {
        color: #00bc96;
        font-weight: bold;
    }
    .status-down {
        color: #ff4b4b;
        font-weight: bold;
    }
    .status-warning {
        color: #ffa600;
        font-weight: bold;
    }
    .status-critical {
        color: #ff2b2b;
        font-weight: bold;
        animation: pulse 1.5s infinite;
    }
    @keyframes pulse {
        0% { opacity: 1; }
        50% { opacity: 0.5; }
        100% { opacity: 1; }
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 2px;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: rgba(0,0,0,0.2);
        border-radius: 4px 4px 0 0;
    }
    .stTabs [aria-selected="true"] {
        background-color: rgba(0,188,150,0.2);
        border-bottom: 2px solid #00bc96;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ---------- Session State ----------
def init_session():
    defaults = {
        "tsdb_manager": TSDBManager(),
        "sql_manager": SQLMonitorManager(),
        "web_render": WebRender(title="Monitoring Dashboard", refresh_interval=30),
        "tsdb_configs": [],
        "sql_configs": [],
        "auto_refresh": False,
        "refresh_interval": 30,
        "last_refresh": datetime.now(),
        "demo_mode": True,
        "query_history": [],
        "active_tab": "Dashboard"
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_session()

# ---------- Helpers ----------
def status_badge(status: str) -> str:
    classes = {
        "up": "status-up", "ok": "status-up", "connected": "status-up",
        "down": "status-down", "error": "status-down", "disconnected": "status-down",
        "warning": "status-warning",
        "critical": "status-critical"
    }
    cls = classes.get(status, "status-warning")
    return f'<span class="{cls}">● {status.upper()}</span>'


def generate_demo_data_tsdb(source: str, points: int = 50) -> pd.DataFrame:
    """Génère des données de démonstration pour les TSDB."""
    now = datetime.now()
    timestamps = [(now - timedelta(minutes=i*2)) for i in range(points)]
    timestamps.reverse()
    
    import random
    random.seed(42)
    
    if "prometheus" in source.lower() or "victoria" in source.lower():
        # CPU/Memory style metrics
        data = {
            "timestamp": timestamps,
            "cpu_usage": [30 + random.gauss(0, 10) + 20 * (i/points) for i in range(points)],
            "memory_usage": [45 + random.gauss(0, 8) + 15 * (i/points) for i in range(points)],
            "disk_io": [10 + random.gauss(0, 5) for i in range(points)],
            "network_rx": [100 + random.gauss(0, 30) for i in range(points)],
        }
    elif "influx" in source.lower():
        # IoT/Sensor style
        data = {
            "time": timestamps,
            "temperature": [22 + random.gauss(0, 2) + 5 * (i/points) for i in range(points)],
            "humidity": [50 + random.gauss(0, 5) for i in range(points)],
            "pressure": [1013 + random.gauss(0, 3) for i in range(points)],
        }
    else:
        data = {
            "timestamp": timestamps,
            "value1": [random.gauss(50, 10) for _ in range(points)],
            "value2": [random.gauss(100, 20) for _ in range(points)],
        }
    
    df = pd.DataFrame(data)
    return df


def generate_demo_sql_metrics() -> List[SQLMetric]:
    """Génère des métriques SQL de démo."""
    import random
    return [
        SQLMetric("active_connections", float(random.randint(5, 45)), "conn", "performance", threshold_warning=40, threshold_critical=80),
        SQLMetric("total_connections", float(random.randint(10, 60)), "conn", "performance", threshold_warning=50, threshold_critical=100),
        SQLMetric("db_size", float(random.randint(100, 2000)), "MB", "storage"),
        SQLMetric("slow_queries", float(random.randint(0, 8)), "queries", "performance", threshold_warning=5, threshold_critical=10),
        SQLMetric("cache_hit_ratio", float(random.randint(85, 99)), "%", "performance", threshold_warning=90, threshold_critical=80),
        SQLMetric("deadlocks", float(random.randint(0, 2)), "count", "performance", threshold_warning=1, threshold_critical=5),
        SQLMetric("transactions", float(random.randint(1000, 50000)), "count", "performance"),
        SQLMetric("table_count", float(random.randint(10, 200)), "tables", "general"),
    ]


# ========== SIDEBAR ==========
with st.sidebar:
    st.markdown("<div class='main-header'>📊 Monitor</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-header'>TSDB + SQL Unified</div>", unsafe_allow_html=True)
    
    st.divider()
    
    # Navigation
    nav = st.radio(
        "Navigation",
        ["Dashboard", "TSDB Sources", "SQL Sources", "Query Explorer", "Logs & Status"],
        index=["Dashboard", "TSDB Sources", "SQL Sources", "Query Explorer", "Logs & Status"].index(st.session_state.active_tab)
    )
    st.session_state.active_tab = nav
    
    st.divider()
    
    # Global settings
    with st.expander("⚙️ Settings", expanded=False):
        st.session_state.demo_mode = st.toggle("Demo Mode", value=st.session_state.demo_mode,
                                                help="Génère des données fictives si pas de connexion réelle")
        st.session_state.auto_refresh = st.toggle("Auto Refresh", value=st.session_state.auto_refresh)
        st.session_state.refresh_interval = st.slider("Interval (s)", 5, 300, 
                                                      st.session_state.refresh_interval, step=5)
    
    # Quick status
    st.divider()
    st.caption("Quick Status")
    
    tsdb_count = len(st.session_state.tsdb_configs)
    sql_count = len(st.session_state.sql_configs)
    
    c1, c2 = st.columns(2)
    c1.metric("TSDB", tsdb_count)
    c2.metric("SQL", sql_count)
    
    if st.session_state.auto_refresh:
        st.caption(f"⏱️ Auto-refresh: {st.session_state.refresh_interval}s")
        time.sleep(0.5)
        if (datetime.now() - st.session_state.last_refresh).seconds >= st.session_state.refresh_interval:
            st.session_state.last_refresh = datetime.now()
            st.rerun()


# ========== DASHBOARD ==========
if nav == "Dashboard":
    st.markdown("<div class='main-header'>Monitoring Dashboard</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-header'>Vue unifiée de vos Time-Series DB et bases SQL</div>", unsafe_allow_html=True)
    
    # Top metrics row
    if st.session_state.demo_mode and not st.session_state.tsdb_configs:
        # Demo data
        col1, col2, col3, col4, col5 = st.columns(5)
        
        demo_metrics = [
            ("CPU Usage", "42%", "+2%"),
            ("Memory", "68%", "-5%"),
            ("Disk I/O", "124 MB/s", "+12%"),
            ("Network", "45 Mbps", "-3%"),
            ("Active Alerts", "3", "+1")
        ]
        
        for col, (label, value, delta) in zip([col1, col2, col3, col4, col5], demo_metrics):
            with col:
                st.metric(label, value, delta)
    
    st.divider()
    
    # TSDB Section
    st.subheader("📈 Time-Series Databases")
    
    if not st.session_state.tsdb_configs and not st.session_state.demo_mode:
        st.info("Aucune source TSDB configurée. Allez dans 'TSDB Sources' pour en ajouter.")
    elif st.session_state.demo_mode and not st.session_state.tsdb_configs:
        # Affichage démo TSDB
        tab1, tab2, tab3 = st.tabs(["Prometheus Demo", "InfluxDB Demo", "VictoriaMetrics Demo"])
        
        with tab1:
            df = generate_demo_data_tsdb("prometheus", 30)
            vr = VisualRender(df=df, dataX=df['timestamp'].tolist(), dataY=df['cpu_usage'].tolist())
            fig1 = vr.render_line("CPU Usage Over Time", "Time", "CPU %", "#00bc96")
            st.plotly_chart(fig1, use_container_width=True)
            
            col_a, col_b = st.columns(2)
            with col_a:
                vr2 = VisualRender(df=df, dataX=df['timestamp'].tolist(), dataY=df['memory_usage'].tolist())
                fig2 = vr2.render_area("Memory Usage", "Time", "Memory %", "#ffa600")
                st.plotly_chart(fig2, use_container_width=True)
            with col_b:
                vrg = VisualRenderGeneral(data=[
                    {"name": "CPU", "value": 42},
                    {"name": "Memory", "value": 68},
                    {"name": "Disk", "value": 23},
                    {"name": "Network", "value": 45}
                ])
                fig3 = vrg.render_pie("Resource Distribution")
                st.plotly_chart(fig3, use_container_width=True)
        
        with tab2:
            df = generate_demo_data_tsdb("influxdb", 30)
            vr = VisualRender(df=df, dataX=df['time'].tolist(), dataY=df['temperature'].tolist())
            fig = vr.render_line("Temperature Sensor", "Time", "°C", "#ff6b6b")
            st.plotly_chart(fig, use_container_width=True)
            
            col_a, col_b = st.columns(2)
            with col_a:
                vr2 = VisualRender(df=df, dataX=df['time'].tolist(), dataY=df['humidity'].tolist())
                fig2 = vr2.render_bar("Humidity Levels", "Time", "%", "#4ecdc4")
                st.plotly_chart(fig2, use_container_width=True)
            with col_b:
                vrg = VisualRenderGeneral(data=[{"label": "Alertes", "value": 2, "unit": "", "delta": "+1"}])
                cards = vrg.render_stats_cards()
                for c in cards:
                    st.metric(c['title'], c['value'], c.get('delta'))
        
        with tab3:
            df = generate_demo_data_tsdb("victoriametrics", 40)
            vr = VisualRender(df=df, dataX=df['timestamp'].tolist(), dataY=df['network_rx'].tolist())
            fig = vr.render_line("Network RX", "Time", "KB/s", "#00bc96")
            st.plotly_chart(fig, use_container_width=True)
    else:
        # Affichage des connecteurs réels configurés
        for cfg in st.session_state.tsdb_configs:
            with st.expander(f"🔌 {cfg['name']} ({cfg['type']})", expanded=True):
                # Health check
                conn = st.session_state.tsdb_manager.get_connector(cfg['name'])
                if conn:
                    health = conn.health_check()
                    status = health.get('status', 'unknown')
                    st.markdown(f"Status: {status_badge(status)}", unsafe_allow_html=True)
                    
                    if health.get('latency_ms'):
                        st.caption(f"Latency: {health['latency_ms']:.1f}ms | Version: {health.get('version', 'N/A')}")
                
                # Si données disponibles, les afficher
                st.caption("Données temps réel (configurez une requête dans Query Explorer)")
    
    st.divider()
    
    # SQL Section
    st.subheader("🗄️ SQL Databases")
    
    if not st.session_state.sql_configs and not st.session_state.demo_mode:
        st.info("Aucune source SQL configurée. Allez dans 'SQL Sources' pour en ajouter.")
    elif st.session_state.demo_mode and not st.session_state.sql_configs:
        # Affichage démo SQL
        metrics = generate_demo_sql_metrics()
        
        # Cards metrics
        cols = st.columns(4)
        for i, metric in enumerate(metrics[:4]):
            with cols[i]:
                status = metric.status()
                color = {"ok": "normal", "warning": "off", "critical": "inverse"}.get(status, "normal")
                st.metric(
                    f"{metric.name} ({metric.category})",
                    f"{metric.value:.1f} {metric.unit}",
                    delta=None,
                    delta_color=color
                )
        
        # Detailed table
        st.divider()
        st.caption("All SQL Metrics")
        
        metric_data = []
        for m in metrics:
            metric_data.append({
                "Metric": m.name,
                "Value": f"{m.value:.1f} {m.unit}",
                "Category": m.category,
                "Status": m.status().upper(),
                "Warning At": m.threshold_warning if m.threshold_warning else "-",
                "Critical At": m.threshold_critical if m.threshold_critical else "-"
            })
        
        vrg = VisualRenderGeneral(data=metric_data)
        fig_table = vrg.render_table("SQL Metrics Detail")
        st.plotly_chart(fig_table, use_container_width=True)
        
        # Gauges for critical metrics
        st.divider()
        st.caption("Key Performance Indicators")
        gcols = st.columns(3)
        gauge_metrics = [m for m in metrics if m.category == "performance"][:3]
        for col, m in zip(gcols, gauge_metrics):
            with col:
                max_val = m.threshold_critical * 1.2 if m.threshold_critical else m.value * 2
                vr = VisualRender()
                fig = vr.render_gauge(m.name, m.value, max_val, 
                                      color="#00bc96" if m.status() == "ok" else "#ffa600" if m.status() == "warning" else "#ff4b4b")
                st.plotly_chart(fig, use_container_width=True)
    else:
        for cfg in st.session_state.sql_configs:
            with st.expander(f"🗄️ {cfg['name']} ({cfg['engine']})", expanded=True):
                mon = st.session_state.sql_manager.get_monitor(cfg['name'])
                if mon:
                    health = mon.health_check()
                    status = "up" if health.get('connected') else "down"
                    st.markdown(f"Status: {status_badge(status)}", unsafe_allow_html=True)
                    st.caption(f"Dialect: {mon._dialect} | Latency: {health.get('latency_ms', 0):.1f}ms")
                    
                    metrics = mon.get_metrics()
                    if metrics:
                        mcols = st.columns(min(len(metrics), 4))
                        for col, m in zip(mcols, metrics[:4]):
                            with col:
                                status = m.status()
                                color = {"ok": "normal", "warning": "off", "critical": "inverse"}.get(status, "normal")
                                st.metric(m.name, f"{m.value:.1f} {m.unit}", delta_color=color)


# ========== TSDB SOURCES ==========
elif nav == "TSDB Sources":
    st.markdown("<div class='main-header'>TSDB Configuration</div>", unsafe_allow_html=True)
    st.markdown("Configurez vos sources Time-Series: InfluxDB, VictoriaMetrics, Prometheus")
    
    col_form, col_list = st.columns([1, 1])
    
    with col_form:
        st.subheader("➕ Add Source")
        
        with st.form("tsdb_form"):
            tsdb_type = st.selectbox("Type", ["InfluxDB v1", "InfluxDB v2", "VictoriaMetrics", "Prometheus"])
            name = st.text_input("Name", placeholder="production-prometheus")
            url = st.text_input("URL", placeholder="http://localhost:9090")
            
            # Auth
            auth_type = st.selectbox("Auth", ["None", "Basic", "Bearer Token"])
            username = ""
            password = ""
            token = ""
            if auth_type == "Basic":
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
            elif auth_type == "Bearer Token":
                token = st.text_input("Token", type="password")
            
            # Options spécifiques
            if "InfluxDB v2" in tsdb_type:
                org = st.text_input("Organization")
                bucket = st.text_input("Bucket")
            elif "InfluxDB v1" in tsdb_type:
                database = st.text_input("Database")
            else:
                org = ""
                bucket = ""
                database = ""
            
            submitted = st.form_submit_button("💾 Save & Test", use_container_width=True)
            
            if submitted and name and url:
                cfg = {
                    "name": name, "type": tsdb_type, "url": url,
                    "auth_type": auth_type, "username": username,
                    "password": password, "token": token
                }
                
                # Créer le connecteur
                try:
                    if "InfluxDB v2" in tsdb_type:
                        cfg["org"] = org
                        cfg["bucket"] = bucket
                        conn = InfluxDBConnector(url, version="2", org=org, bucket=bucket, token=token)
                    elif "InfluxDB v1" in tsdb_type:
                        cfg["database"] = database
                        conn = InfluxDBConnector(url, version="1", database=database, 
                                                  username=username, password=password)
                    elif "VictoriaMetrics" in tsdb_type:
                        conn = VictoriaMetricsConnector(url, username=username, password=password)
                    else:  # Prometheus
                        conn = PrometheusConnector(url, username=username, password=password)
                    
                    st.session_state.tsdb_manager.add_connector(name, conn)
                    st.session_state.tsdb_configs.append(cfg)
                    
                    # Test
                    health = conn.health_check()
                    if health.get("status") == "up":
                        st.success(f"✅ {name} connecté! ({health.get('latency_ms', 0):.0f}ms)")
                    else:
                        st.warning(f"⚠️ {name} sauvegardé mais test échoué: {health.get('error', 'Unknown')}")
                
                except Exception as e:
                    st.error(f"Erreur: {e}")
    
    with col_list:
        st.subheader("📋 Sources Configurées")
        
        if not st.session_state.tsdb_configs:
            st.info("Aucune source TSDB")
        else:
            for i, cfg in enumerate(st.session_state.tsdb_configs):
                with st.container():
                    c1, c2, c3 = st.columns([3, 1, 1])
                    c1.markdown(f"**{cfg['name']}**  \n`{cfg['type']}` @ `{cfg['url']}`")
                    
                    conn = st.session_state.tsdb_manager.get_connector(cfg['name'])
                    if conn:
                        health = conn.health_check()
                        status = health.get('status', 'unknown')
                        c2.markdown(status_badge(status), unsafe_allow_html=True)
                    else:
                        c2.markdown(status_badge('unknown'), unsafe_allow_html=True)
                    
                    if c3.button("🗑️", key=f"del_tsdb_{i}"):
                        st.session_state.tsdb_manager.remove_connector(cfg['name'])
                        st.session_state.tsdb_configs.pop(i)
                        st.rerun()
                    
                    st.divider()


# ========== SQL SOURCES ==========
elif nav == "SQL Sources":
    st.markdown("<div class='main-header'>SQL Configuration</div>", unsafe_allow_html=True)
    st.markdown("Configurez vos bases de données SQL à monitorer")
    
    col_form, col_list = st.columns([1, 1])
    
    with col_form:
        st.subheader("➕ Add SQL Source")
        
        with st.form("sql_form"):
            engine = st.selectbox("Engine", ["PostgreSQL", "MySQL", "SQLite", "SQL Server"])
            name = st.text_input("Name", placeholder="production-db")
            
            if engine == "SQLite":
                connection_string = st.text_input("Database Path", placeholder="/path/to/db.sqlite")
                if connection_string and not connection_string.startswith("sqlite"):
                    connection_string = f"sqlite:///{connection_string}"
            else:
                host = st.text_input("Host", placeholder="localhost")
                port = st.number_input("Port", value=5432 if engine == "PostgreSQL" else 3306 if engine == "MySQL" else 1433)
                database = st.text_input("Database", placeholder="mydb")
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                
                if engine == "PostgreSQL":
                    connection_string = f"postgresql://{username}:{password}@{host}:{port}/{database}"
                elif engine == "MySQL":
                    connection_string = f"mysql+pymysql://{username}:{password}@{host}:{port}/{database}"
                else:  # SQL Server
                    connection_string = f"mssql+pyodbc://{username}:{password}@{host}:{port}/{database}?driver=ODBC+Driver+17+for+SQL+Server"
            
            submitted = st.form_submit_button("💾 Save & Test", use_container_width=True)
            
            if submitted and name and connection_string:
                cfg = {
                    "name": name, "engine": engine,
                    "connection_string": connection_string
                }
                
                try:
                    monitor = SQLMonitor(connection_string, name)
                    connected = monitor.connect()
                    
                    if connected:
                        st.session_state.sql_manager.add_monitor(name, connection_string)
                        st.session_state.sql_configs.append(cfg)
                        st.success(f"✅ {name} connecté!")
                        
                        # Fetch initial metrics
                        metrics = monitor.get_metrics()
                        st.caption(f"{len(metrics)} métriques récupérées")
                    else:
                        st.error(f"❌ Connexion échouée: {monitor._status.get('error', 'Unknown')}")
                        if st.session_state.demo_mode:
                            st.session_state.sql_configs.append(cfg)
                            st.info("Sauvegardé en mode démo")
                
                except Exception as e:
                    st.error(f"Erreur: {e}")
                    if st.session_state.demo_mode:
                        st.session_state.sql_configs.append(cfg)
                        st.info("Sauvegardé en mode démo")
    
    with col_list:
        st.subheader("📋 SQL Sources")
        
        if not st.session_state.sql_configs:
            st.info("Aucune source SQL")
        else:
            for i, cfg in enumerate(st.session_state.sql_configs):
                with st.container():
                    c1, c2, c3 = st.columns([3, 1, 1])
                    c1.markdown(f"**{cfg['name']}**  \n`{cfg['engine']}`")
                    
                    mon = st.session_state.sql_manager.get_monitor(cfg['name'])
                    if mon:
                        health = mon.health_check()
                        status = "up" if health.get('connected') else "down"
                        c2.markdown(status_badge(status), unsafe_allow_html=True)
                    else:
                        c2.markdown(status_badge('unknown'), unsafe_allow_html=True)
                    
                    if c3.button("🗑️", key=f"del_sql_{i}"):
                        st.session_state.sql_manager.remove_monitor(cfg['name'])
                        st.session_state.sql_configs.pop(i)
                        st.rerun()
                    
                    st.divider()


# ========== QUERY EXPLORER ==========
elif nav == "Query Explorer":
    st.markdown("<div class='main-header'>Query Explorer</div>", unsafe_allow_html=True)
    st.markdown("Exécutez des requêtes sur vos sources configurées")
    
    source_type = st.radio("Source Type", ["TSDB (PromQL/InfluxQL)", "SQL"], horizontal=True)
    
    if source_type == "TSDB (PromQL/InfluxQL)":
        if not st.session_state.tsdb_configs:
            st.warning("Aucune source TSDB configurée")
        else:
            source = st.selectbox("Source", [c['name'] for c in st.session_state.tsdb_configs])
            query = st.text_area("Query", "up", placeholder="PromQL: up | InfluxQL: SELECT * FROM ...")
            
            col_btn, col_opts = st.columns([1, 3])
            with col_btn:
                execute = st.button("▶️ Execute", use_container_width=True)
            with col_opts:
                if st.session_state.tsdb_configs:
                    cfg = next((c for c in st.session_state.tsdb_configs if c['name'] == source), None)
                    if cfg and "InfluxDB" in cfg['type']:
                        st.caption("Langage: InfluxQL / Flux")
                    else:
                        st.caption("Langage: PromQL")
            
            if execute and query:
                conn = st.session_state.tsdb_manager.get_connector(source)
                if conn or st.session_state.demo_mode:
                    with st.spinner("Exécution..."):
                        if conn:
                            try:
                                results = conn.query(query)
                            except Exception as e:
                                st.error(f"Erreur: {e}")
                                results = []
                        else:
                            # Demo data
                            results = [
                                {"timestamp": "2024-01-01T00:00:00Z", "value": 1, "instance": "localhost:9090"},
                                {"timestamp": "2024-01-01T00:01:00Z", "value": 1, "instance": "localhost:9090"},
                                {"timestamp": "2024-01-01T00:02:00Z", "value": 0, "instance": "localhost:9090"},
                            ]
                        
                        st.session_state.query_history.append({
                            "timestamp": datetime.now().isoformat(),
                            "source": source, "query": query, "results_count": len(results)
                        })
                        
                        st.subheader(f"Results ({len(results)} rows)")
                        
                        if results and not any('error' in r for r in results[:1]):
                            # Try to display as chart if time-series data
                            df = pd.DataFrame(results)
                            st.dataframe(df, use_container_width=True)
                            
                            # Auto-render if numeric data available
                            numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns.tolist()
                            if numeric_cols and 'timestamp' in df.columns or 'time' in df.columns:
                                time_col = 'timestamp' if 'timestamp' in df.columns else 'time'
                                st.divider()
                                st.subheader("Visualisation")
                                
                                vr = VisualRender(df=df, dataX=df[time_col].tolist(), 
                                                  dataY=df[numeric_cols[0]].tolist())
                                fig = vr.render_line(f"{numeric_cols[0]} over Time", time_col, numeric_cols[0])
                                st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.json(results)
    
    else:  # SQL
        if not st.session_state.sql_configs:
            st.warning("Aucune source SQL configurée")
        else:
            source = st.selectbox("Source", [c['name'] for c in st.session_state.sql_configs])
            query = st.text_area("SQL Query", "SELECT * FROM information_schema.tables LIMIT 10",
                                placeholder="SELECT ...")
            
            if st.button("▶️ Execute", use_container_width=True):
                mon = st.session_state.sql_manager.get_monitor(source)
                if mon or st.session_state.demo_mode:
                    with st.spinner("Exécution..."):
                        if mon:
                            results = mon.execute_query(query)
                        else:
                            results = [
                                {"table_name": "users", "table_type": "BASE TABLE"},
                                {"table_name": "orders", "table_type": "BASE TABLE"},
                                {"table_name": "products", "table_type": "BASE TABLE"},
                            ]
                        
                        st.session_state.query_history.append({
                            "timestamp": datetime.now().isoformat(),
                            "source": source, "query": query, "results_count": len(results)
                        })
                        
                        st.subheader(f"Results ({len(results)} rows)")
                        
                        if results and not any('error' in r for r in results[:1]):
                            df = pd.DataFrame(results)
                            st.dataframe(df, use_container_width=True)
                            
                            # Visualisation générale si données numériques
                            numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns.tolist()
                            if numeric_cols:
                                st.divider()
                                vrg = VisualRenderGeneral(data=df.to_dict('records'))
                                fig = vrg.render_table("Query Results")
                                st.plotly_chart(fig, use_container_width=True)
                        else:
                            if results and 'error' in results[0]:
                                st.error(results[0]['error'])
                            st.json(results)


# ========== LOGS & STATUS ==========
elif nav == "Logs & Status":
    st.markdown("<div class='main-header'>System Logs & Status</div>", unsafe_allow_html=True)
    
    # TSDB Status
    st.subheader("TSDB Connectors Status")
    if st.session_state.tsdb_manager.connectors:
        health_data = []
        for name, conn in st.session_state.tsdb_manager.connectors.items():
            health = conn.health_check()
            health_data.append({
                "Connector": name,
                "Type": conn.name,
                "Status": health.get('status', 'unknown'),
                "Latency (ms)": f"{health.get('latency_ms', 0):.1f}",
                "Version": health.get('version', 'N/A'),
                "URL": conn.base_url
            })
        
        vrg = VisualRenderGeneral(data=health_data)
        fig = vrg.render_table("TSDB Health Status")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Aucun connecteur TSDB actif")
    
    st.divider()
    
    # SQL Status
    st.subheader("SQL Monitors Status")
    if st.session_state.sql_manager.monitors:
        sql_health = []
        for name, mon in st.session_state.sql_manager.monitors.items():
            health = mon.health_check()
            sql_health.append({
                "Monitor": name,
                "Dialect": mon._dialect,
                "Connected": "Yes" if health.get('connected') else "No",
                "Latency (ms)": f"{health.get('latency_ms', 0):.1f}",
                "Last Check": health.get('last_check', 'Never'),
                "Error": health.get('error', '-')[:50]
            })
        
        vrg = VisualRenderGeneral(data=sql_health)
        fig = vrg.render_table("SQL Health Status")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Aucun moniteur SQL actif")
    
    st.divider()
    
    # Query History
    st.subheader("Query History")
    if st.session_state.query_history:
        hist_df = pd.DataFrame(st.session_state.query_history)
        st.dataframe(hist_df, use_container_width=True)
    else:
        st.info("Aucune requête exécutée")
    
    st.divider()
    
    # FetchApi Logs
    st.subheader("API Request Logs")
    
    # Collect logs from all fetchers
    all_logs = []
    for name, conn in st.session_state.tsdb_manager.connectors.items():
        logs = conn._fetcher.get_logs()
        for log in logs:
            log['connector'] = name
            all_logs.append(log)
    
    if all_logs:
        logs_df = pd.DataFrame(all_logs)
        st.dataframe(logs_df, use_container_width=True)
    else:
        st.info("Aucun log de requête")
    
    # System Info
    st.divider()
    st.subheader("Application Info")
    
    info_col1, info_col2 = st.columns(2)
    with info_col1:
        st.json({
            "app": "Monitoring Dashboard",
            "version": "1.0.0",
            "render_engine": "Plotly + Streamlit",
            "tsdb_support": ["InfluxDB v1/v2", "VictoriaMetrics", "Prometheus"],
            "sql_support": ["PostgreSQL", "MySQL", "SQLite", "SQL Server"],
            "fetch_pattern": "Asyncio + Coroutines + Semaphore",
            "classes": ["VisualRender", "VisualRenderGeneral", "WebRender", "FetchApi"]
        })
    with info_col2:
        st.markdown("""
        **Architecture:**
        - `VisualRender` : Graphiques X/Y (line, bar, area, gauge)
        - `VisualRenderGeneral` : Visualisations générales (pie, table, heatmap)
        - `WebRender` : Assemblage dashboard avec layout grid
        - `FetchApi` : Client HTTP async avec retry, circuit-breaker, semaphore
        
        **Connecteurs TSDB:**
        - InfluxDB v1/v2 (InfluxQL / Flux)
        - VictoriaMetrics (PromQL)
        - Prometheus (PromQL + Alert/Rules/Targets)
        
        **Monitoring SQL:**
        - Multi-engine via SQLAlchemy
        - Métriques temps réel (connections, cache, locks...)
        - Exploration schéma et requêtes lentes
        """)

st.divider()
st.caption("Monitoring Dashboard v1.0 | Built with Streamlit + Plotly | Async FetchAPI")
