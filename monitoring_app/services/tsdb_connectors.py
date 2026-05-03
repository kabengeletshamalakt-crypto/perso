"""
Connecteurs TSDB - InfluxDB, VictoriaMetrics, Prometheus
Utilise FetchApi pour les requêtes HTTP aux APIs respectives.
"""

from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
import json
import urllib.parse

from models.fetcher import FetchApi, FetchResponse


class BaseTSDBConnector:
    """Classe de base pour tous les connecteurs TSDB."""
    
    def __init__(self, base_url: str, name: str = "TSDB"):
        self.base_url = base_url.rstrip("/")
        self.name = name
        self._fetcher = FetchApi("GET")
        self._status = {"connected": False, "last_check": None, "latency_ms": 0}
    
    def set_auth(self, username: str = None, password: str = None, token: str = None):
        """Configure l'authentification."""
        if token:
            self._fetcher.set_bearer_token(token)
        elif username and password:
            self._fetcher.set_auth(username, password)
    
    def health_check(self) -> Dict[str, Any]:
        """Vérifie la santé de la connexion."""
        raise NotImplementedError()
    
    def query(self, q: str, **kwargs) -> List[Dict[str, Any]]:
        """Exécute une requête et retourne des données normalisées."""
        raise NotImplementedError()
    
    def get_status(self) -> Dict[str, Any]:
        return self._status.copy()


class InfluxDBConnector(BaseTSDBConnector):
    """
    Connecteur InfluxDB (v1 et v2).
    
    API v1: /query?q=...&db=...
    API v2: /api/v2/query with Flux
    """
    
    def __init__(self, base_url: str, version: str = "2", org: str = "", 
                 bucket: str = "", token: str = "", username: str = "", 
                 password: str = "", database: str = ""):
        super().__init__(base_url, f"InfluxDB-{version}")
        self.version = version
        self.org = org
        self.bucket = bucket
        self.database = database
        
        if token:
            self.set_auth(token=token)
        elif username and password:
            self.set_auth(username=username, password=password)
    
    def health_check(self) -> Dict[str, Any]:
        """Ping InfluxDB."""
        if self.version == "2":
            url = f"{self.base_url}/health"
        else:
            url = f"{self.base_url}/ping"
        
        resp = self._fetcher.fetch_sync(url)
        self._status["last_check"] = datetime.now().isoformat()
        self._status["latency_ms"] = resp.duration_ms
        
        if resp.success:
            self._status["connected"] = True
            return {"status": "up", "latency_ms": resp.duration_ms, "version": self.version}
        else:
            self._status["connected"] = False
            return {"status": "down", "error": resp.error, "latency_ms": resp.duration_ms}
    
    def query(self, query_str: str = None, measurement: str = None, 
              fields: List[str] = None, start: str = "-1h", 
              stop: str = "now()", aggregate_window: str = None,
              limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Exécute une requête InfluxDB.
        
        Args:
            query_str: Requête brute (InfluxQL ou Flux)
            measurement: Nom de la measurement (si pas de query_str)
            fields: Champs à sélectionner
            start: Début de la fenêtre temporelle
            stop: Fin de la fenêtre temporelle
            aggregate_window: Fenêtre d'agrégation (ex: '5m')
            limit: Limite de résultats
        """
        if self.version == "2":
            return self._query_v2(query_str, measurement, fields, start, stop, aggregate_window, limit)
        else:
            return self._query_v1(query_str, measurement, fields, start, stop, limit)
    
    def _query_v1(self, query_str, measurement, fields, start, stop, limit):
        """Requête InfluxDB v1 via InfluxQL."""
        if not query_str:
            field_str = ", ".join(fields) if fields else "*"
            if not measurement:
                measurement = ".*"  # Toutes les measurements
            query_str = f"SELECT {field_str} FROM {measurement} WHERE time > {start} LIMIT {limit}"
        
        url = f"{self.base_url}/query"
        params = {"q": query_str}
        if self.database:
            params["db"] = self.database
        
        resp = self._fetcher.fetch_sync(url, params=params)
        
        if not resp.success:
            return [{"error": resp.error, "source": "influxdb_v1"}]
        
        return self._parse_influx_v1_response(resp.data)
    
    def _query_v2(self, query_str, measurement, fields, start, stop, aggregate_window, limit):
        """Requête InfluxDB v2 via Flux."""
        if not query_str:
            field_filter = ""
            if fields and measurement:
                field_filters = " or ".join([f'r._field == "{f}"' for f in fields])
                field_filter = f"|> filter(fn: (r) => {field_filters})"
            
            agg = ""
            if aggregate_window:
                agg = f'|> aggregateWindow(every: {aggregate_window}, fn: mean, createEmpty: false)'
            
            flux = f'''
            from(bucket: "{self.bucket}")
            |> range(start: {start}, stop: {stop})
            |> filter(fn: (r) => r._measurement == "{measurement}")
            {field_filter}
            {agg}
            |> limit(n: {limit})
            '''
            query_str = flux
        
        url = f"{self.base_url}/api/v2/query"
        params = {"org": self.org}
        payload = {"query": query_str}
        
        # Pour InfluxDB v2, on POST avec le flux dans le body
        self._fetcher.method = "POST"
        resp = self._fetcher.fetch_sync(url, payload=payload, params=params)
        self._fetcher.method = "GET"
        
        if not resp.success:
            return [{"error": resp.error, "source": "influxdb_v2"}]
        
        return self._parse_influx_v2_response(resp.data)
    
    def _parse_influx_v1_response(self, data):
        """Parse la réponse JSON InfluxDB v1."""
        results = []
        if isinstance(data, dict) and "results" in data:
            for result in data["results"]:
                if "series" in result:
                    for series in result["series"]:
                        columns = series.get("columns", [])
                        values = series.get("values", [])
                        for row in values:
                            entry = dict(zip(columns, row))
                            entry["_measurement"] = series.get("name", "unknown")
                            results.append(entry)
        return results if results else [{"raw": data, "source": "influxdb_v1"}]
    
    def _parse_influx_v2_response(self, data):
        """Parse la réponse CSV/JSON InfluxDB v2."""
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
        elif isinstance(data, str):
            # Réponse CSV de Flux
            lines = [l for l in data.strip().split("\n") if l and not l.startswith("#")]
            if len(lines) < 2:
                return [{"raw": data, "source": "influxdb_v2"}]
            headers = lines[0].split(",")
            results = []
            for line in lines[1:]:
                values = line.split(",")
                entry = {}
                for h, v in zip(headers, values):
                    entry[h] = v
                results.append(entry)
            return results
        return [{"raw": str(data), "source": "influxdb_v2"}]
    
    def list_measurements(self) -> List[str]:
        """Liste les measurements disponibles."""
        if self.version == "1":
            url = f"{self.base_url}/query"
            params = {"q": "SHOW MEASUREMENTS", "db": self.database}
            resp = self._fetcher.fetch_sync(url, params=params)
            if resp.success and isinstance(resp.data, dict):
                # Parsing rapide
                try:
                    vals = resp.data["results"][0]["series"][0]["values"]
                    return [v[0] for v in vals]
                except (KeyError, IndexError):
                    pass
        else:
            # Flux: lister buckets puis measurements
            flux = f'''
            import "influxdata/influxdb/v1"
            v1.measurements(bucket: "{self.bucket}")
            '''
            url = f"{self.base_url}/api/v2/query"
            self._fetcher.method = "POST"
            resp = self._fetcher.fetch_sync(url, payload={"query": flux}, params={"org": self.org})
            self._fetcher.method = "GET"
            # Parsing simplifié
            if isinstance(resp.data, str):
                lines = resp.data.strip().split("\n")
                if len(lines) > 2:
                    return [l.split(",")[-1] for l in lines[2:] if l]
        return []


class VictoriaMetricsConnector(BaseTSDBConnector):
    """
    Connecteur VictoriaMetrics.
    
    API compatible Prometheus: /api/v1/query, /api/v1/query_range
    """
    
    def __init__(self, base_url: str, tenant: str = "", 
                 username: str = "", password: str = ""):
        super().__init__(base_url, "VictoriaMetrics")
        self.tenant = tenant
        if username and password:
            self.set_auth(username, password)
    
    def health_check(self) -> Dict[str, Any]:
        """Vérifie VictoriaMetrics via /api/v1/status/buildinfo."""
        url = f"{self.base_url}/api/v1/status/buildinfo"
        resp = self._fetcher.fetch_sync(url)
        self._status["last_check"] = datetime.now().isoformat()
        self._status["latency_ms"] = resp.duration_ms
        
        if resp.success:
            self._status["connected"] = True
            version = resp.data.get("data", {}).get("version", "unknown") if isinstance(resp.data, dict) else "unknown"
            return {"status": "up", "latency_ms": resp.duration_ms, "version": version}
        else:
            self._status["connected"] = False
            return {"status": "down", "error": resp.error, "latency_ms": resp.duration_ms}
    
    def query(self, query_str: str, time: Optional[str] = None,
              start: Optional[str] = None, end: Optional[str] = None,
              step: str = "15s", limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Exécute une requête PromQL.
        
        Args:
            query_str: Expression PromQL
            time: Timestamp pour instant query
            start: Début pour range query
            end: Fin pour range query
            step: Intervalle pour range query
            limit: Limite de résultats
        """
        if start and end:
            # Range query
            url = f"{self.base_url}/api/v1/query_range"
            params = {
                "query": query_str,
                "start": start,
                "end": end,
                "step": step,
                "limit": limit
            }
        else:
            # Instant query
            url = f"{self.base_url}/api/v1/query"
            params = {"query": query_str}
            if time:
                params["time"] = time
        
        resp = self._fetcher.fetch_sync(url, params=params)
        
        if not resp.success:
            return [{"error": resp.error, "source": "victoriametrics"}]
        
        return self._parse_prometheus_response(resp.data)
    
    def query_range(self, query_str: str, start: str, end: str, 
                    step: str = "15s", limit: int = 10000) -> List[Dict[str, Any]]:
        """Helper pour range query."""
        return self.query(query_str, start=start, end=end, step=step, limit=limit)
    
    def _parse_prometheus_response(self, data: Any) -> List[Dict[str, Any]]:
        """Parse la réponse Prometheus/VictoriaMetrics."""
        if not isinstance(data, dict):
            return [{"raw": str(data), "source": "victoriametrics"}]
        
        status = data.get("status", "unknown")
        if status != "success":
            return [{"error": data.get("error", "Unknown error"), "source": "victoriametrics"}]
        
        result_data = data.get("data", {})
        result_type = result_data.get("resultType", "")
        results = result_data.get("result", [])
        
        parsed = []
        for r in results:
            metric = r.get("metric", {})
            if "value" in r:
                # Instant vector
                entry = metric.copy()
                entry["timestamp"] = r["value"][0]
                entry["value"] = float(r["value"][1])
                parsed.append(entry)
            elif "values" in r:
                # Range vector
                for ts, val in r["values"]:
                    entry = metric.copy()
                    entry["timestamp"] = ts
                    entry["value"] = float(val)
                    parsed.append(entry)
        
        return parsed if parsed else [{"raw": data, "source": "victoriametrics"}]
    
    def list_metrics(self, match: str = "", limit: int = 1000) -> List[str]:
        """Liste les noms de métriques disponibles."""
        url = f"{self.base_url}/api/v1/label/__name__/values"
        params = {"limit": limit}
        if match:
            params["match[]"] = match
        
        resp = self._fetcher.fetch_sync(url, params=params)
        if resp.success and isinstance(resp.data, dict):
            data = resp.data.get("data", [])
            return data if isinstance(data, list) else []
        return []
    
    def get_labels(self, metric_name: str) -> List[str]:
        """Liste les labels pour une métrique."""
        url = f"{self.base_url}/api/v1/labels"
        params = {"match[]": metric_name}
        resp = self._fetcher.fetch_sync(url, params=params)
        if resp.success and isinstance(resp.data, dict):
            return resp.data.get("data", [])
        return []


class PrometheusConnector(BaseTSDBConnector):
    """
    Connecteur Prometheus natif.
    API identique à VictoriaMetrics sur les endpoints standard.
    """
    
    def __init__(self, base_url: str, username: str = "", password: str = ""):
        super().__init__(base_url, "Prometheus")
        if username and password:
            self.set_auth(username, password)
    
    def health_check(self) -> Dict[str, Any]:
        """Vérifie Prometheus via /-/healthy ou /api/v1/status/buildinfo."""
        # Essayer /-/healthy d'abord
        resp = self._fetcher.fetch_sync(f"{self.base_url}/-/healthy")
        self._status["last_check"] = datetime.now().isoformat()
        self._status["latency_ms"] = resp.duration_ms
        
        if resp.success:
            self._status["connected"] = True
            return {"status": "up", "latency_ms": resp.duration_ms}
        
        # Fallback sur buildinfo
        resp = self._fetcher.fetch_sync(f"{self.base_url}/api/v1/status/buildinfo")
        self._status["latency_ms"] = resp.duration_ms
        if resp.success:
            self._status["connected"] = True
            version = resp.data.get("data", {}).get("version", "unknown") if isinstance(resp.data, dict) else "unknown"
            return {"status": "up", "latency_ms": resp.duration_ms, "version": version}
        
        self._status["connected"] = False
        return {"status": "down", "error": resp.error, "latency_ms": resp.duration_ms}
    
    def query(self, query_str: str, time: Optional[str] = None,
              start: Optional[str] = None, end: Optional[str] = None,
              step: str = "15s", limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Exécute une requête PromQL.
        """
        if start and end:
            url = f"{self.base_url}/api/v1/query_range"
            params = {
                "query": query_str,
                "start": start,
                "end": end,
                "step": step,
                "limit": limit
            }
        else:
            url = f"{self.base_url}/api/v1/query"
            params = {"query": query_str}
            if time:
                params["time"] = time
        
        resp = self._fetcher.fetch_sync(url, params=params)
        
        if not resp.success:
            return [{"error": resp.error, "source": "prometheus"}]
        
        return self._parse_prometheus_response(resp.data)
    
    def _parse_prometheus_response(self, data: Any) -> List[Dict[str, Any]]:
        """Parse la réponse Prometheus (même format que VM)."""
        if not isinstance(data, dict):
            return [{"raw": str(data), "source": "prometheus"}]
        
        status = data.get("status", "unknown")
        if status != "success":
            return [{"error": data.get("error", "Unknown error"), "source": "prometheus"}]
        
        result_data = data.get("data", {})
        results = result_data.get("result", [])
        
        parsed = []
        for r in results:
            metric = r.get("metric", {})
            if "value" in r:
                entry = metric.copy()
                entry["timestamp"] = r["value"][0]
                entry["value"] = float(r["value"][1])
                parsed.append(entry)
            elif "values" in r:
                for ts, val in r["values"]:
                    entry = metric.copy()
                    entry["timestamp"] = ts
                    entry["value"] = float(val)
                    parsed.append(entry)
        
        return parsed if parsed else [{"raw": data, "source": "prometheus"}]
    
    def targets(self) -> List[Dict[str, Any]]:
        """Liste les targets scrape de Prometheus."""
        url = f"{self.base_url}/api/v1/targets"
        resp = self._fetcher.fetch_sync(url)
        if resp.success and isinstance(resp.data, dict):
            data = resp.data.get("data", {})
            active = data.get("activeTargets", [])
            dropped = data.get("droppedTargets", [])
            return [{"active": active, "dropped": dropped}]
        return [{"error": "Failed to fetch targets", "source": "prometheus"}]
    
    def alerts(self) -> List[Dict[str, Any]]:
        """Recupere les alertes actives."""
        url = f"{self.base_url}/api/v1/alerts"
        resp = self._fetcher.fetch_sync(url)
        if resp.success and isinstance(resp.data, dict):
            return resp.data.get("data", {}).get("alerts", [])
        return []
    
    def rules(self) -> List[Dict[str, Any]]:
        """Recupere les regles d'alerting/recording."""
        url = f"{self.base_url}/api/v1/rules"
        resp = self._fetcher.fetch_sync(url)
        if resp.success and isinstance(resp.data, dict):
            groups = resp.data.get("data", {}).get("groups", [])
            return groups
        return []
    
    def list_metrics(self, match: str = "", limit: int = 1000) -> List[str]:
        """Liste les métriques."""
        url = f"{self.base_url}/api/v1/label/__name__/values"
        params = {"limit": limit}
        if match:
            params["match[]"] = match
        resp = self._fetcher.fetch_sync(url, params=params)
        if resp.success and isinstance(resp.data, dict):
            return resp.data.get("data", [])
        return []


class TSDBManager:
    """
    Gère plusieurs connexions TSDB simultanées.
    """
    
    def __init__(self):
        self.connectors: Dict[str, BaseTSDBConnector] = {}
    
    def add_connector(self, name: str, connector: BaseTSDBConnector):
        """Ajoute un connecteur nommé."""
        self.connectors[name] = connector
    
    def remove_connector(self, name: str):
        """Retire un connecteur."""
        self.connectors.pop(name, None)
    
    def health_check_all(self) -> Dict[str, Dict[str, Any]]:
        """Health check sur tous les connecteurs."""
        return {name: conn.health_check() for name, conn in self.connectors.items()}
    
    def query_all(self, query_map: Dict[str, str]) -> Dict[str, List[Dict]]:
        """
        Exécute des requêtes sur plusieurs TSDB en parallèle.
        query_map: {connector_name: query_string}
        """
        results = {}
        for name, query in query_map.items():
            if name in self.connectors:
                results[name] = self.connectors[name].query(query)
            else:
                results[name] = [{"error": f"Connector '{name}' not found"}]
        return results
    
    def get_connector(self, name: str) -> Optional[BaseTSDBConnector]:
        return self.connectors.get(name)
    
    def list_connectors(self) -> List[str]:
        return list(self.connectors.keys())
