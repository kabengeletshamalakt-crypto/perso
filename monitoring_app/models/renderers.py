"""
Modèles de rendu visuel pour le dashboard de monitoring.
Implémente les classes VisualRender, VisualRenderGeneral et WebRender.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots


@dataclass
class VisualRender:
    """
    Rendu visuel pour des données structurées en X/Y (séries temporelles, courbes...).
    
    Attributes:
        visual: Object graphique Plotly (fig)
        dataX: liste des données axe X
        dataY: liste des données axe Y
        df: DataFrame pandas source
    """
    visual: Optional[go.Figure] = None
    dataX: List[Any] = field(default_factory=list)
    dataY: List[Any] = field(default_factory=list)
    df: Optional[pd.DataFrame] = None
    
    def __post_init__(self):
        if self.df is not None and not self.dataX and not self.dataY:
            self._auto_extract()
    
    def _auto_extract(self):
        """Extrait automatiquement dataX/dataY depuis le DataFrame."""
        if self.df is not None and len(self.df.columns) >= 2:
            self.dataX = self.df.iloc[:, 0].tolist()
            self.dataY = self.df.iloc[:, 1].tolist()
    
    def render_line(self, title: str = "Line Chart", x_label: str = "X", y_label: str = "Y",
                    color: str = "#00bc96") -> go.Figure:
        """Génère un graphique linéaire."""
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=self.dataX, y=self.dataY,
            mode='lines+markers',
            line=dict(color=color, width=2),
            name=y_label
        ))
        fig.update_layout(
            title=title, xaxis_title=x_label, yaxis_title=y_label,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
        )
        self.visual = fig
        return fig
    
    def render_bar(self, title: str = "Bar Chart", x_label: str = "X", y_label: str = "Y",
                   color: str = "#00bc96") -> go.Figure:
        """Génère un graphique en barres."""
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=self.dataX, y=self.dataY,
            marker_color=color,
            name=y_label
        ))
        fig.update_layout(
            title=title, xaxis_title=x_label, yaxis_title=y_label,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
        )
        self.visual = fig
        return fig
    
    def render_area(self, title: str = "Area Chart", x_label: str = "X", y_label: str = "Y",
                    color: str = "#00bc96") -> go.Figure:
        """Génère un graphique en aire."""
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=self.dataX, y=self.dataY,
            fill='tozeroy',
            mode='lines',
            line=dict(color=color, width=2),
            fillcolor=f"rgba{tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + (0.3,)}".replace("'", ""),
            name=y_label
        ))
        fig.update_layout(
            title=title, xaxis_title=x_label, yaxis_title=y_label,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
        )
        self.visual = fig
        return fig
    
    def render_gauge(self, title: str = "Gauge", value: float = 0, max_val: float = 100,
                     color: str = "#00bc96") -> go.Figure:
        """Génère un indicateur gauge."""
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=value,
            title={'text': title},
            gauge={
                'axis': {'range': [0, max_val]},
                'bar': {'color': color},
                'steps': [
                    {'range': [0, max_val*0.5], 'color': "lightgray"},
                    {'range': [max_val*0.5, max_val*0.8], 'color': "gray"},
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': max_val*0.9
                }
            }
        ))
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        self.visual = fig
        return fig
    
    def update_data(self, dataX: List[Any], dataY: List[Any]):
        """Met à jour les données et régénère le graphique si présent."""
        self.dataX = dataX
        self.dataY = dataY
        if self.df is not None:
            self.df = pd.DataFrame({'x': dataX, 'y': dataY})


@dataclass
class VisualRenderGeneral:
    """
    Rendu visuel général pour des données non-structurées en X/Y.
    
    Attributes:
        visual: Object graphique Plotly (fig)
        data: liste de données génériques (dicts, objets...)
    """
    visual: Optional[go.Figure] = None
    data: List[Any] = field(default_factory=list)
    
    def render_pie(self, names_field: str = "name", values_field: str = "value",
                   title: str = "Pie Chart") -> go.Figure:
        """Génère un camembert depuis une liste de dicts."""
        if not self.data:
            fig = go.Figure()
            fig.update_layout(title="No Data", template="plotly_dark")
            self.visual = fig
            return fig
        
        names = [d.get(names_field, f"Item {i}") for i, d in enumerate(self.data)]
        values = [d.get(values_field, 0) for d in self.data]
        
        fig = go.Figure(data=[go.Pie(labels=names, values=values, hole=.3)])
        fig.update_layout(title=title, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        self.visual = fig
        return fig
    
    def render_table(self, title: str = "Data Table") -> go.Figure:
        """Génère un tableau depuis une liste de dicts."""
        if not self.data:
            fig = go.Figure()
            fig.update_layout(title="No Data", template="plotly_dark")
            self.visual = fig
            return fig
        
        df = pd.DataFrame(self.data)
        fig = go.Figure(data=[go.Table(
            header=dict(
                values=list(df.columns),
                fill_color='#00bc96',
                align='left',
                font=dict(color='white', size=12)
            ),
            cells=dict(
                values=[df[col] for col in df.columns],
                fill_color='rgba(0,0,0,0.3)',
                align='left',
                font=dict(color='white', size=11)
            )
        )])
        fig.update_layout(title=title, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        self.visual = fig
        return fig
    
    def render_heatmap(self, title: str = "Heatmap") -> go.Figure:
        """Génère une heatmap si data est une matrice 2D."""
        if not self.data:
            fig = go.Figure()
            fig.update_layout(title="No Data", template="plotly_dark")
            self.visual = fig
            return fig
        
        df = pd.DataFrame(self.data)
        fig = px.imshow(df, color_continuous_scale='Viridis')
        fig.update_layout(title=title, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        self.visual = fig
        return fig
    
    def render_stats_cards(self) -> List[Dict[str, Any]]:
        """Retourne une liste de dicts pour des cartes statistiques Streamlit."""
        cards = []
        for item in self.data[:6]:  # Max 6 cards
            if isinstance(item, dict):
                cards.append({
                    'title': item.get('label', item.get('name', 'Metric')),
                    'value': item.get('value', item.get('count', 0)),
                    'delta': item.get('delta', None),
                    'unit': item.get('unit', '')
                })
        return cards
    
    def update_data(self, data: List[Any]):
        """Met à jour les données générales."""
        self.data = data


@dataclass
class WebRender:
    """
    Rendu global du dashboard. Agrège plusieurs composants visuels.
    
    Attributes:
        components: liste d'objets visuels (VisualRender, VisualRenderGeneral, dicts config)
    """
    components: List[Any] = field(default_factory=list)
    layout: str = "grid"  # grid, tabs, sidebar
    title: str = "Monitoring Dashboard"
    refresh_interval: int = 30  # seconds
    
    def add_component(self, component: Any, position: Optional[int] = None):
        """Ajoute un composant au dashboard."""
        if position is not None:
            self.components.insert(position, component)
        else:
            self.components.append(component)
    
    def remove_component(self, index: int):
        """Retire un composant par index."""
        if 0 <= index < len(self.components):
            self.components.pop(index)
    
    def render_grid(self, columns: int = 2) -> List[go.Figure]:
        """Retourne les figures pour un rendu en grille Streamlit."""
        figures = []
        for comp in self.components:
            if isinstance(comp, (VisualRender, VisualRenderGeneral)):
                if comp.visual is not None:
                    figures.append(comp.visual)
            elif isinstance(comp, go.Figure):
                figures.append(comp)
        return figures
    
    def render_summary(self) -> Dict[str, Any]:
        """Retourne un résumé du dashboard."""
        return {
            'title': self.title,
            'component_count': len(self.components),
            'layout': self.layout,
            'refresh_interval': self.refresh_interval,
            'component_types': [type(c).__name__ for c in self.components]
        }
    
    def get_streamlit_columns(self, st, n_cols: int = 2):
        """Retourne les colonnes Streamlit configurées."""
        return st.columns(n_cols)
    
    def auto_layout(self, st):
        """Rend automatiquement les composants dans Streamlit."""
        st.title(self.title)
        st.markdown("---")
        
        cols = self.get_streamlit_columns(st, n_cols=2)
        col_idx = 0
        
        for i, comp in enumerate(self.components):
            with cols[col_idx]:
                if isinstance(comp, VisualRender):
                    if comp.visual:
                        st.plotly_chart(comp.visual, use_container_width=True, key=f"vr_{i}")
                elif isinstance(comp, VisualRenderGeneral):
                    if comp.visual:
                        st.plotly_chart(comp.visual, use_container_width=True, key=f"vrg_{i}")
                    else:
                        cards = comp.render_stats_cards()
                        for card in cards[:2]:
                            delta = card.get('delta')
                            st.metric(card['title'], f"{card['value']} {card['unit']}", delta)
                elif isinstance(comp, go.Figure):
                    st.plotly_chart(comp, use_container_width=True, key=f"fig_{i}")
                elif isinstance(comp, dict) and comp.get('type') == 'metric':
                    delta = comp.get('delta')
                    st.metric(comp['title'], comp['value'], delta)
                elif isinstance(comp, dict) and comp.get('type') == 'markdown':
                    st.markdown(comp['content'])
            
            col_idx = (col_idx + 1) % len(cols)
            if col_idx == 0 and i < len(self.components) - 1:
                cols = self.get_streamlit_columns(st, n_cols=2)
