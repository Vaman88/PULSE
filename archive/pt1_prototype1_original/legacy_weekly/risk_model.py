"""Second stage: 30 Arkansas synthetic event-risk signals from public features.

Ten event families at three horizons are calibrated to synthetic event tags.
The labels are scenario labels, not observed Arkansas outbreak reports.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

EVENTS = {
    "influenza": ("flu_2023_24", "flu_2024_25"),
    "covid_summer": ("covid_summer_2023_outpatient_pulse", "covid_summer_2024_outpatient_pulse",
                     "covid_summer_2025_outpatient_pulse"),
    "rsv": ("rsv_winter",),
    "antibiotic_shortage": ("amoxicillin_shortage", "antibiotic_supply_pressure"),
    "supply_response": ("dated_supply_outbreak_response",),
    "avian_flu": ("h5n1_monitoring",),
    "drug_access": ("medicaid_unwinding_2023",),
    "naloxone_access": ("naloxone_otc_switch",),
    "allergy": ("spring_allergy",),
    "winter_respiratory": ("winter_respiratory",),
}
HORIZONS = (7, 14, 30)


def event_targets(sales: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    tags = sales[["date", "injected_event_tags"]].copy()
    tags["date"] = pd.to_datetime(tags.date)
    tags["injected_event_tags"] = tags.injected_event_tags.fillna("")
    tags = tags.groupby("date").injected_event_tags.apply(lambda x: set("|".join(x).split("|")))
    tags = tags.reindex(dates, fill_value=set())
    out = pd.DataFrame(index=dates)
    for event, names in EVENTS.items():
        observed = np.array([bool(set(names) & found) for found in tags], dtype=int)
        for horizon in HORIZONS:
            y = np.array([observed[i + 1:i + horizon + 1].any()
                          if i + horizon < len(observed) else np.nan
                          for i in range(len(observed))], dtype=float)
            out[f"risk_{event}_{horizon}d"] = y
    return out


@dataclass
class RiskModel:
    models: dict
    feature_names: list[str]
    embedding_names: list[str]
    context_names: list[str]
    embedding_scaler: StandardScaler | None
    reducer: PCA | None
    context_scaler: StandardScaler

    def transform(self, features: pd.DataFrame) -> np.ndarray:
        context = self.context_scaler.transform(features[self.context_names].to_numpy())
        if self.reducer is None:
            return context
        embedded = self.reducer.transform(self.embedding_scaler.transform(
            features[self.embedding_names].to_numpy()))
        return np.column_stack([embedded, context])

    @classmethod
    def fit(cls, features: pd.DataFrame, targets: pd.DataFrame, train_end: pd.Timestamp):
        mask = ((features.index + pd.Timedelta(days=max(HORIZONS))) < train_end) & targets.notna().all(axis=1)
        if mask.sum() < 100:
            raise ValueError("risk model needs at least 100 dated training origins")
        embedding_names = [name for name in features if name.startswith("flan_")]
        context_names = [name for name in features if name not in embedding_names]
        if not embedding_names or not context_names:
            raise ValueError("risk model requires both FLAN and structured public features")
        training = features.loc[mask]
        context_scaler = StandardScaler().fit(training[context_names].to_numpy())
        context = context_scaler.transform(training[context_names].to_numpy())
        vectors = training[embedding_names].to_numpy()
        if np.var(vectors, axis=0).sum() > 1e-12:
            embedding_scaler = StandardScaler().fit(vectors)
            standardized = embedding_scaler.transform(vectors)
            reducer = PCA(n_components=min(32, len(training) - 1), random_state=20250915).fit(standardized)
            x = np.column_stack([reducer.transform(standardized), context])
        else:
            embedding_scaler = reducer = None
            x = context
        models = {}
        for name in targets:
            y = targets.loc[mask, name].astype(int).to_numpy()
            if np.unique(y).size == 1:
                models[name] = float(y[0])
            else:
                model = LogisticRegression(C=.1, max_iter=500, class_weight="balanced")
                model.fit(x, y)
                models[name] = model
        return cls(models, features.columns.tolist(), embedding_names,
                   context_names, embedding_scaler, reducer, context_scaler)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        x = self.transform(features[self.feature_names])
        result = {}
        for name, model in self.models.items():
            result[name] = np.full(len(x), model) if isinstance(model, float) else model.predict_proba(x)[:, 1]
        return pd.DataFrame(result, index=features.index)
