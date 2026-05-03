"""
FetchApi - Module de récupération de données asynchrone avec coroutines.
Utilise asyncio pour des requêtes HTTP concurrentes sécurisées.
"""

import asyncio
import aiohttp
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Union
from enum import Enum
import json


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


@dataclass
class FetchResponse:
    """Encapsule une réponse HTTP."""
    status: int = 0
    data: Any = None
    headers: Dict[str, str] = field(default_factory=dict)
    duration_ms: float = 0.0
    url: str = ""
    error: Optional[str] = None
    success: bool = False


class FetchApi:
    """
    Client API asynchrone utilisant les coroutines pour boucler en toute sécurité.
    
    Attributes:
        method: Méthode HTTP (GET, POST, etc.)
        data: Données à envoyer (payload)
        output_data: Données reçues/transformées
    """
    
    def __init__(self, method: str = "GET"):
        self.method: str = method.upper()
        self.data: List[Any] = []
        self.output_data: Optional[Any] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout: int = 30
        self._max_retries: int = 3
        self._retry_delay: float = 1.0
        self._headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        self._auth: Optional[tuple] = None
        self._ssl_verify: bool = True
        self._request_log: List[Dict[str, Any]] = []
    
    def set_auth(self, username: str, password: str):
        """Configure l'authentification Basic."""
        self._auth = aiohttp.BasicAuth(username, password)
    
    def set_bearer_token(self, token: str):
        """Configure l'authentification Bearer."""
        self._headers["Authorization"] = f"Bearer {token}"
    
    def set_timeout(self, seconds: int):
        """Configure le timeout."""
        self._timeout = seconds
    
    def set_headers(self, headers: Dict[str, str]):
        """Ajoute/remplace des headers."""
        self._headers.update(headers)
    
    async def _create_session(self):
        """Crée une session aiohttp si non existante."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, headers=self._headers)
        return self._session
    
    async def _close_session(self):
        """Ferme proprement la session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def fetch_one(self, url: str, payload: Optional[Dict] = None, 
                        params: Optional[Dict] = None) -> FetchResponse:
        """
        Effectue une requête HTTP unique avec retry et circuit-breaker pattern.
        """
        session = await self._create_session()
        start_time = time.time()
        last_error = None
        
        for attempt in range(self._max_retries):
            try:
                request_kwargs = {
                    "url": url,
                    "ssl": self._ssl_verify,
                }
                if self._auth:
                    request_kwargs["auth"] = self._auth
                if params:
                    request_kwargs["params"] = params
                if payload and self.method in ["POST", "PUT", "PATCH"]:
                    request_kwargs["json"] = payload
                
                http_method = getattr(session, self.method.lower(), session.get)
                
                async with http_method(**request_kwargs) as response:
                    duration = (time.time() - start_time) * 1000
                    
                    # Lecture du body selon le content-type
                    content_type = response.headers.get('Content-Type', '')
                    if 'application/json' in content_type:
                        try:
                            body = await response.json()
                        except:
                            body = await response.text()
                    else:
                        body = await response.text()
                    
                    success = 200 <= response.status < 300
                    
                    resp = FetchResponse(
                        status=response.status,
                        data=body,
                        headers=dict(response.headers),
                        duration_ms=duration,
                        url=url,
                        error=None if success else f"HTTP {response.status}: {body}",
                        success=success
                    )
                    
                    self._request_log.append({
                        "url": url, "method": self.method, "status": response.status,
                        "duration_ms": duration, "attempt": attempt + 1,
                        "timestamp": time.time()
                    })
                    
                    return resp
                    
            except asyncio.TimeoutError:
                last_error = "Timeout"
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))
            except aiohttp.ClientError as e:
                last_error = str(e)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))
            except Exception as e:
                last_error = str(e)
                break
        
        # Tous les retries échoués
        duration = (time.time() - start_time) * 1000
        resp = FetchResponse(
            status=0, data=None, duration_ms=duration,
            url=url, error=f"Failed after {self._max_retries} attempts: {last_error}",
            success=False
        )
        self._request_log.append({
            "url": url, "method": self.method, "status": 0,
            "duration_ms": duration, "attempt": self._max_retries,
            "timestamp": time.time(), "error": last_error
        })
        return resp
    
    async def fetch_many(self, urls: List[str], payloads: Optional[List[Dict]] = None,
                         params_list: Optional[List[Dict]] = None) -> List[FetchResponse]:
        """
        Effectue plusieurs requêtes HTTP en parallèle avec asyncio.gather.
        """
        payloads = payloads or [None] * len(urls)
        params_list = params_list or [None] * len(urls)
        
        tasks = [
            self.fetch_one(url, payload, params)
            for url, payload, params in zip(urls, payloads, params_list)
        ]
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Conversion des exceptions en FetchResponse d'erreur
        results = []
        for i, resp in enumerate(responses):
            if isinstance(resp, Exception):
                results.append(FetchResponse(
                    status=0, url=urls[i] if i < len(urls) else "",
                    error=str(resp), success=False
                ))
            else:
                results.append(resp)
        
        self.output_data = results
        return results
    
    async def fetch_with_semaphore(self, urls: List[str], max_concurrent: int = 5,
                                    payloads: Optional[List[Dict]] = None,
                                    params_list: Optional[List[Dict]] = None) -> List[FetchResponse]:
        """
        Effectue des requêtes avec limitation de concurrence (semaphore).
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        payloads = payloads or [None] * len(urls)
        params_list = params_list or [None] * len(urls)
        
        async def _fetch_limited(url, payload, params):
            async with semaphore:
                # Petit délai pour éviter le rate limiting
                await asyncio.sleep(0.05)
                return await self.fetch_one(url, payload, params)
        
        tasks = [
            _fetch_limited(url, payload, params)
            for url, payload, params in zip(urls, payloads, params_list)
        ]
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        results = []
        for i, resp in enumerate(responses):
            if isinstance(resp, Exception):
                results.append(FetchResponse(
                    status=0, url=urls[i] if i < len(urls) else "",
                    error=str(resp), success=False
                ))
            else:
                results.append(resp)
        
        self.output_data = results
        return results
    
    async def fetch_stream(self, url: str, callback: Callable[[Any], None]):
        """
        Streaming SSE (Server-Sent Events) pour données temps réel.
        """
        session = await self._create_session()
        try:
            async with session.get(url, headers={"Accept": "text/event-stream"}) as response:
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data:'):
                        data = line[5:].strip()
                        try:
                            parsed = json.loads(data)
                            callback(parsed)
                        except json.JSONDecodeError:
                            callback(data)
        except Exception as e:
            self._request_log.append({"url": url, "error": str(e), "timestamp": time.time()})
        finally:
            pass  # Session gérée globalement
    
    async def close(self):
        """Ferme toutes les connexions."""
        await self._close_session()
    
    def get_logs(self) -> List[Dict[str, Any]]:
        """Retourne les logs de requêtes."""
        return self._request_log.copy()
    
    def clear_logs(self):
        """Vide les logs."""
        self._request_log.clear()
    
    # ---------- Méthodes synchrones helpers pour Streamlit ----------
    
    def fetch_sync(self, url: str, payload: Optional[Dict] = None, 
                   params: Optional[Dict] = None) -> FetchResponse:
        """Version synchrone pour Streamlit (run coroutine)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Si déjà dans un event loop (Streamlit), créer une nouvelle task
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(self.fetch_one(url, payload, params))
            else:
                return loop.run_until_complete(self.fetch_one(url, payload, params))
        except RuntimeError:
            # Pas d'event loop, en créer un nouveau
            return asyncio.run(self.fetch_one(url, payload, params))
    
    def fetch_many_sync(self, urls: List[str], payloads: Optional[List[Dict]] = None,
                        params_list: Optional[List[Dict]] = None) -> List[FetchResponse]:
        """Version synchrone de fetch_many."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(self.fetch_many(urls, payloads, params_list))
            else:
                return loop.run_until_complete(self.fetch_many(urls, payloads, params_list))
        except RuntimeError:
            return asyncio.run(self.fetch_many(urls, payloads, params_list))
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager pour garantir la fermeture."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
                loop.run_until_complete(self.close())
            else:
                loop.run_until_complete(self.close())
        except RuntimeError:
            asyncio.run(self.close())
