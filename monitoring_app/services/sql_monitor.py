"""
SQL Monitor - Monitoring de bases de données SQL.
Supporte PostgreSQL, MySQL, SQLite, SQL Server via SQLAlchemy + requêtes métriques.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import time

try:
    from sqlalchemy import create_engine, text, inspect, Engine
    from sqlalchemy.exc import OperationalError, SQLAlchemyError
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False


@dataclass
class SQLMetric:
    """Métrique SQL mesurée."""
    name: str
    value: float
    unit: str = ""
    category: str = "general"  # general, performance, replication, storage
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    threshold_warning: Optional[float] = None
    threshold_critical: Optional[float] = None
    
    def status(self) -> str:
        """Retourne le statut basé sur les seuils."""
        if self.threshold_critical is not None and self.value >= self.threshold_critical:
            return "critical"
        if self.threshold_warning is not None and self.value >= self.threshold_warning:
            return "warning"
        return "ok"


class SQLMonitor:
    """
    Moniteur SQL multi-engine.
    
    Fournit:
    - Health check de connexion
    - Métriques de performance (connections, slow queries, locks...)
    - Exploration du schéma (tables, indexes, sizes)
    - Exécution de requêtes custom
    """
    
    def __init__(self, connection_string: str, name: str = "SQL DB"):
        self.connection_string = connection_string
        self.name = name
        self._engine: Optional[Any] = None
        self._dialect = self._detect_dialect(connection_string)
        self._status = {"connected": False, "last_check": None, "latency_ms": 0, "error": None}
        self._metrics_cache: List[SQLMetric] = []
        self._cache_time: Optional[float] = None
        self._cache_ttl = 30  # seconds
    
    def _detect_dialect(self, conn_str: str) -> str:
        """Détecte le dialecte SQL depuis la connection string."""
        if conn_str.startswith("postgresql"):
            return "postgresql"
        elif conn_str.startswith("mysql") or conn_str.startswith("pymysql") or conn_str.startswith("mariadb"):
            return "mysql"
        elif conn_str.startswith("sqlite"):
            return "sqlite"
        elif conn_str.startswith("mssql") or conn_str.startswith("pyodbc"):
            return "mssql"
        return "unknown"
    
    def connect(self) -> bool:
        """Établit la connexion."""
        if not SQLALCHEMY_AVAILABLE:
            self._status["error"] = "SQLAlchemy not installed"
            return False
        
        try:
            start = time.time()
            self._engine = create_engine(self.connection_string, pool_pre_ping=True, echo=False)
            # Test connection
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            
            self._status["connected"] = True
            self._status["last_check"] = datetime.now().isoformat()
            self._status["latency_ms"] = (time.time() - start) * 1000
            self._status["error"] = None
            return True
        except Exception as e:
            self._status["connected"] = False
            self._status["last_check"] = datetime.now().isoformat()
            self._status["error"] = str(e)
            return False
    
    def disconnect(self):
        """Ferme la connexion."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
        self._status["connected"] = False
    
    def health_check(self) -> Dict[str, Any]:
        """Vérifie la santé de la connexion SQL."""
        if not self._status["connected"]:
            self.connect()
        
        if self._status["connected"]:
            # Vérification supplémentaire avec ping
            try:
                start = time.time()
                with self._engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                self._status["latency_ms"] = (time.time() - start) * 1000
                self._status["last_check"] = datetime.now().isoformat()
            except Exception as e:
                self._status["connected"] = False
                self._status["error"] = str(e)
        
        return self._status.copy()
    
    def get_metrics(self, force_refresh: bool = False) -> List[SQLMetric]:
        """
        Récupère les métriques de la base de données.
        """
        now = time.time()
        if not force_refresh and self._cache_time and (now - self._cache_time) < self._cache_ttl:
            return self._metrics_cache.copy()
        
        if not self._status["connected"]:
            self.connect()
        
        if not self._status["connected"]:
            return [SQLMetric(name="connection", value=0, unit="status", category="general")]
        
        metrics = []
        
        try:
            if self._dialect == "postgresql":
                metrics = self._get_postgres_metrics()
            elif self._dialect == "mysql":
                metrics = self._get_mysql_metrics()
            elif self._dialect == "sqlite":
                metrics = self._get_sqlite_metrics()
            elif self._dialect == "mssql":
                metrics = self._get_mssql_metrics()
        except Exception as e:
            metrics.append(SQLMetric(name="metrics_error", value=0, unit="error", category="general"))
            self._status["error"] = str(e)
        
        self._metrics_cache = metrics
        self._cache_time = now
        return metrics.copy()
    
    def _get_postgres_metrics(self) -> List[SQLMetric]:
        """Métriques PostgreSQL spécifiques."""
        metrics = []
        with self._engine.connect() as conn:
            # Active connections
            result = conn.execute(text("""
                SELECT count(*) as count FROM pg_stat_activity WHERE state = 'active'
            """))
            active = result.scalar() or 0
            metrics.append(SQLMetric(name="active_connections", value=float(active), unit="conn", 
                                       category="performance", threshold_warning=80, threshold_critical=95))
            
            # Total connections
            result = conn.execute(text("SELECT count(*) as count FROM pg_stat_activity"))
            total = result.scalar() or 0
            metrics.append(SQLMetric(name="total_connections", value=float(total), unit="conn", 
                                       category="performance", threshold_warning=100, threshold_critical=150))
            
            # Database size
            result = conn.execute(text("""
                SELECT pg_database_size(current_database()) as size
            """))
            size = result.scalar() or 0
            metrics.append(SQLMetric(name="db_size", value=float(size) / (1024*1024), unit="MB", 
                                       category="storage"))
            
            # Slow queries (> 1s)
            result = conn.execute(text("""
                SELECT count(*) as count FROM pg_stat_activity 
                WHERE state = 'active' AND now() - query_start > interval '1 second'
            """))
            slow = result.scalar() or 0
            metrics.append(SQLMetric(name="slow_queries", value=float(slow), unit="queries", 
                                       category="performance", threshold_warning=5, threshold_critical=10))
            
            # Cache hit ratio
            result = conn.execute(text("""
                SELECT round(blks_hit*100.0/(blks_hit+blks_read), 2) as ratio 
                FROM pg_stat_database WHERE datname = current_database()
            """))
            ratio = result.scalar() or 0
            metrics.append(SQLMetric(name="cache_hit_ratio", value=float(ratio), unit="%", 
                                       category="performance", threshold_warning=90, threshold_critical=80))
            
            # Deadlocks
            result = conn.execute(text("""
                SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()
            """))
            deadlocks = result.scalar() or 0
            metrics.append(SQLMetric(name="deadlocks", value=float(deadlocks), unit="count", 
                                       category="performance", threshold_warning=1, threshold_critical=5))
            
            # Transaction rate (tps approx)
            result = conn.execute(text("""
                SELECT xact_commit + xact_rollback as tps 
                FROM pg_stat_database WHERE datname = current_database()
            """))
            tps = result.scalar() or 0
            metrics.append(SQLMetric(name="transactions", value=float(tps), unit="count", 
                                       category="performance"))
            
            # Table count
            result = conn.execute(text("""
                SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'
            """))
            tables = result.scalar() or 0
            metrics.append(SQLMetric(name="table_count", value=float(tables), unit="tables", 
                                       category="general"))
        
        return metrics
    
    def _get_mysql_metrics(self) -> List[SQLMetric]:
        """Métriques MySQL/MariaDB spécifiques."""
        metrics = []
        with self._engine.connect() as conn:
            # Active connections (Threads_connected)
            result = conn.execute(text("SHOW STATUS LIKE 'Threads_connected'"))
            row = result.fetchone()
            if row:
                metrics.append(SQLMetric(name="active_connections", value=float(row[1]), unit="conn",
                                           category="performance", threshold_warning=80, threshold_critical=150))
            
            # Slow queries
            result = conn.execute(text("SHOW STATUS LIKE 'Slow_queries'"))
            row = result.fetchone()
            if row:
                metrics.append(SQLMetric(name="slow_queries", value=float(row[1]), unit="queries",
                                           category="performance", threshold_warning=10, threshold_critical=50))
            
            # Queries per second
            result = conn.execute(text("SHOW STATUS LIKE 'Queries'"))
            row = result.fetchone()
            if row:
                metrics.append(SQLMetric(name="total_queries", value=float(row[1]), unit="queries",
                                           category="performance"))
            
            # Uptime
            result = conn.execute(text("SHOW STATUS LIKE 'Uptime'"))
            row = result.fetchone()
            if row:
                metrics.append(SQLMetric(name="uptime", value=float(row[1]) / 3600, unit="hours",
                                           category="general"))
            
            # Database size
            result = conn.execute(text("""
                SELECT SUM(data_length + index_length) / 1024 / 1024 as size 
                FROM information_schema.tables WHERE table_schema = DATABASE()
            """))
            row = result.fetchone()
            if row and row[0]:
                metrics.append(SQLMetric(name="db_size", value=float(row[0]), unit="MB",
                                           category="storage"))
            
            # Table count
            result = conn.execute(text("""
                SELECT count(*) FROM information_schema.tables WHERE table_schema = DATABASE()
            """))
            row = result.fetchone()
            if row:
                metrics.append(SQLMetric(name="table_count", value=float(row[0]), unit="tables",
                                           category="general"))
            
            # Innodb buffer pool hit rate (approximation)
            result = conn.execute(text("""
                SHOW STATUS LIKE 'Innodb_buffer_pool_read_requests'
            """))
            read_req = result.fetchone()
            result = conn.execute(text("""
                SHOW STATUS LIKE 'Innodb_buffer_pool_reads'
            """))
            reads = result.fetchone()
            if read_req and reads and int(read_req[1]) > 0:
                hit_rate = (1 - int(reads[1]) / int(read_req[1])) * 100
                metrics.append(SQLMetric(name="innodb_hit_rate", value=round(hit_rate, 2), unit="%",
                                           category="performance", threshold_warning=90, threshold_critical=80))
        
        return metrics
    
    def _get_sqlite_metrics(self) -> List[SQLMetric]:
        """Métriques SQLite."""
        metrics = []
        with self._engine.connect() as conn:
            # Page count
            result = conn.execute(text("PRAGMA page_count"))
            pages = result.scalar() or 0
            
            # Page size
            result = conn.execute(text("PRAGMA page_size"))
            page_size = result.scalar() or 0
            
            size_mb = (pages * page_size) / (1024 * 1024)
            metrics.append(SQLMetric(name="db_size", value=size_mb, unit="MB", category="storage"))
            
            # Table count
            result = conn.execute(text("""
                SELECT count(*) FROM sqlite_master WHERE type='table'
            """))
            tables = result.scalar() or 0
            metrics.append(SQLMetric(name="table_count", value=float(tables), unit="tables", category="general"))
            
            # Index count
            result = conn.execute(text("""
                SELECT count(*) FROM sqlite_master WHERE type='index'
            """))
            indexes = result.scalar() or 0
            metrics.append(SQLMetric(name="index_count", value=float(indexes), unit="indexes", category="general"))
        
        return metrics
    
    def _get_mssql_metrics(self) -> List[SQLMetric]:
        """Métriques SQL Server."""
        metrics = []
        with self._engine.connect() as conn:
            # Active connections
            result = conn.execute(text("""
                SELECT COUNT(dbid) as count FROM sys.sysprocesses WHERE status = 'sleeping'
            """))
            active = result.scalar() or 0
            metrics.append(SQLMetric(name="active_connections", value=float(active), unit="conn",
                                       category="performance"))
            
            # Database size
            result = conn.execute(text("""
                SELECT SUM(size * 8.0 / 1024) FROM sys.database_files
            """))
            size = result.scalar() or 0
            metrics.append(SQLMetric(name="db_size", value=float(size), unit="MB", category="storage"))
        
        return metrics
    
    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Exécute une requête SQL arbitraire et retourne les résultats."""
        if not self._status["connected"]:
            self.connect()
        
        if not self._status["connected"]:
            return [{"error": "Not connected"}]
        
        try:
            with self._engine.connect() as conn:
                result = conn.execute(text(query), params or {})
                rows = [dict(row._mapping) for row in result.mappings()]
                return rows
        except Exception as e:
            return [{"error": str(e), "query": query}]
    
    def get_schema(self) -> Dict[str, Any]:
        """Explore le schéma de la base de données."""
        if not self._status["connected"]:
            self.connect()
        
        if not self._status["connected"]:
            return {"error": "Not connected"}
        
        try:
            inspector = inspect(self._engine)
            schema_info = {
                "tables": {},
                "views": inspector.get_view_names(),
                "schemas": inspector.get_schema_names() if hasattr(inspector, 'get_schema_names') else []
            }
            
            for table_name in inspector.get_table_names():
                columns = inspector.get_columns(table_name)
                pk = inspector.get_pk_constraint(table_name)
                indexes = inspector.get_indexes(table_name)
                fk = inspector.get_foreign_keys(table_name)
                
                schema_info["tables"][table_name] = {
                    "columns": [{"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable", True)} 
                                for c in columns],
                    "primary_key": pk,
                    "indexes": indexes,
                    "foreign_keys": fk
                }
            
            return schema_info
        except Exception as e:
            return {"error": str(e)}
    
    def get_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Récupère les requêtes lentes (PostgreSQL/MySQL)."""
        if self._dialect == "postgresql":
            query = """
                SELECT pid, now() - query_start as duration, state, query 
                FROM pg_stat_activity 
                WHERE state = 'active' AND now() - query_start > interval '1 second'
                ORDER BY duration DESC
                LIMIT :limit
            """
            return self.execute_query(query, {"limit": limit})
        elif self._dialect == "mysql":
            query = """
                SELECT * FROM performance_schema.events_statements_history_long 
                ORDER BY TIMER_WAIT DESC
                LIMIT :limit
            """
            return self.execute_query(query, {"limit": limit})
        return [{"error": "Slow queries not supported for this dialect"}]
    
    def get_locks(self) -> List[Dict[str, Any]]:
        """Récupère les locks actifs."""
        if self._dialect == "postgresql":
            query = """
                SELECT l.locktype, l.relation::regclass, l.mode, l.granted,
                       a.usename, a.query, a.pid
                FROM pg_locks l
                JOIN pg_stat_activity a ON l.pid = a.pid
                WHERE NOT l.granted OR l.locktype = 'relation'
                ORDER BY l.granted, l.pid
            """
            return self.execute_query(query)
        elif self._dialect == "mysql":
            query = """
                SELECT * FROM information_schema.innodb_locks
                UNION
                SELECT * FROM information_schema.innodb_lock_waits
            """
            return self.execute_query(query)
        return [{"error": "Locks monitoring not supported for this dialect"}]


class SQLMonitorManager:
    """Gère plusieurs connexions SQL."""
    
    def __init__(self):
        self.monitors: Dict[str, SQLMonitor] = {}
    
    def add_monitor(self, name: str, connection_string: str):
        """Ajoute un moniteur SQL."""
        self.monitors[name] = SQLMonitor(connection_string, name)
    
    def remove_monitor(self, name: str):
        self.monitors.pop(name, None)
    
    def health_check_all(self) -> Dict[str, Dict[str, Any]]:
        return {name: mon.health_check() for name, mon in self.monitors.items()}
    
    def get_all_metrics(self) -> Dict[str, List[SQLMetric]]:
        return {name: mon.get_metrics() for name, mon in self.monitors.items()}
    
    def get_monitor(self, name: str) -> Optional[SQLMonitor]:
        return self.monitors.get(name)
