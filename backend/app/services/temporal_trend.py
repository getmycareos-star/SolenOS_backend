from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import math
import statistics


class TrendType(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    FLUCTUATING = "fluctuating"
    INTERMITTENT = "intermittent"
    RECURRING = "recurring"
    CYCLICAL = "cyclical"
    INSUFFICIENT_DATA = "insufficient_data"
    UNKNOWN = "unknown"
    CONFLICTED = "conflicted"


class MeasurementType(str, Enum):
    VITAL_SIGN = "vital_sign"
    SYMPTOM_SEVERITY = "symptom_severity"
    MEDICATION_DOSE = "medication_dose"
    MOBILITY_SCORE = "mobility_score"
    COGNITIVE_SCORE = "cognitive_score"
    BEHAVIOR_FREQUENCY = "behavior_frequency"
    WEIGHT = "weight"
    BLOOD_PRESSURE = "blood_pressure"
    BLOOD_GLUCOSE = "blood_glucose"
    PAIN_SCORE = "pain_score"
    SLEEP_HOURS = "sleep_hours"
    APPOINTMENT_ADHERENCE = "appointment_adherence"
    FALL_COUNT = "fall_count"
    CUSTOM = "custom"


@dataclass
class TimeSeriesPoint:
    """A single measurement in a time series"""
    timestamp: datetime
    value: float
    measurement_type: MeasurementType
    unit: str = ""
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


@dataclass
class TrendResult:
    """Result of trend analysis"""
    trend_type: TrendType
    measurement_type: MeasurementType
    slope: float  # per day
    slope_per_month: float
    r_squared: float
    p_value: Optional[float]
    confidence: float
    data_points: int
    time_span_days: float
    start_value: float
    end_value: float
    change_percent: float
    supporting_evidence: List[str]
    warnings: List[str]


@dataclass
class CyclicalPattern:
    """Detected cyclical pattern"""
    period_days: float
    amplitude: float
    phase_offset_days: float
    confidence: float
    peaks: List[datetime]
    troughs: List[datetime]


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def detect_trend(
    points: List[TimeSeriesPoint],
    min_points: int = 3,
    min_time_span_days: int = 7,
    max_gap_days: int = 60
) -> TrendResult:
    """
    Detect trend in a time series of measurements.
    Uses linear regression with robust statistics.
    """
    if len(points) < min_points:
        return TrendResult(
            trend_type=TrendType.INSUFFICIENT_DATA,
            measurement_type=points[0].measurement_type if points else MeasurementType.CUSTOM,
            slope=0, slope_per_month=0, r_squared=0, p_value=None,
            confidence=0, data_points=len(points), time_span_days=0,
            start_value=0, end_value=0, change_percent=0,
            supporting_evidence=[], warnings=["Insufficient data points"]
        )
    
    # Sort by timestamp
    sorted_points = sorted(points, key=lambda p: p.timestamp)
    
    # Check time span
    time_span = (sorted_points[-1].timestamp - sorted_points[0].timestamp).total_seconds() / 86400
    if time_span < min_time_span_days:
        return TrendResult(
            trend_type=TrendType.INSUFFICIENT_DATA,
            measurement_type=sorted_points[0].measurement_type,
            slope=0, slope_per_month=0, r_squared=0, p_value=None,
            confidence=0, data_points=len(points), time_span_days=time_span,
            start_value=sorted_points[0].value, end_value=sorted_points[-1].value,
            change_percent=0,
            supporting_evidence=[], warnings=[f"Time span too short: {time_span:.1f} days"]
        )
    
    # Check for large gaps
    warnings = []
    for i in range(1, len(sorted_points)):
        gap = (sorted_points[i].timestamp - sorted_points[i-1].timestamp).total_seconds() / 86400
        if gap > max_gap_days:
            warnings.append(f"Large gap of {gap:.1f} days between points {i-1} and {i}")
    
    # Prepare data for regression
    x = []  # days since first point
    y = []  # values
    weights = []  # confidence weights
    
    base_time = sorted_points[0].timestamp
    for p in sorted_points:
        days = (p.timestamp - base_time).total_seconds() / 86400
        x.append(days)
        y.append(p.value)
        weights.append(p.confidence)
    
    # Weighted linear regression
    n = len(x)
    sum_w = sum(weights)
    sum_wx = sum(w * xi for w, xi in zip(weights, x))
    sum_wy = sum(w * yi for w, yi in zip(weights, y))
    sum_wxx = sum(w * xi * xi for w, xi in zip(weights, x))
    sum_wxy = sum(w * xi * yi for w, xi, yi in zip(weights, x, y))
    
    denom = sum_w * sum_wxx - sum_wx * sum_wx
    if denom == 0:
        return TrendResult(
            trend_type=TrendType.UNKNOWN,
            measurement_type=sorted_points[0].measurement_type,
            slope=0, slope_per_month=0, r_squared=0, p_value=None,
            confidence=0, data_points=n, time_span_days=time_span,
            start_value=y[0], end_value=y[-1], change_percent=0,
            supporting_evidence=[], warnings=warnings + ["Singular matrix in regression"]
        )
    
    slope = (sum_w * sum_wxy - sum_wx * sum_wy) / denom
    intercept = (sum_wy * sum_wxx - sum_wx * sum_wxy) / denom
    
    # Calculate R-squared
    y_pred = [intercept + slope * xi for xi in x]
    ss_tot = sum(w * (yi - sum_wy / sum_w) ** 2 for w, yi in zip(weights, y))
    ss_res = sum(w * (yi - ypi) ** 2 for w, yi, ypi in zip(weights, y, y_pred))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    
    # Calculate standard error and p-value (approximate)
    se_slope = math.sqrt(ss_res / (n - 2) / sum_wxx * sum_w) if n > 2 else float('inf')
    t_stat = abs(slope / se_slope) if se_slope > 0 else 0
    
    # Approximate p-value using t-distribution
    p_value = None
    if n > 2:
        # Simple approximation
        if t_stat > 2.5:
            p_value = 0.01
        elif t_stat > 2.0:
            p_value = 0.05
        elif t_stat > 1.5:
            p_value = 0.1
        else:
            p_value = 0.2
    
    # Determine trend type
    slope_per_month = slope * 30.44
    start_val = y[0]
    end_val = y[-1]
    change_percent = ((end_val - start_val) / abs(start_val) * 100) if start_val != 0 else 0
    
    evidence = []
    trend_type = TrendType.UNKNOWN
    
    if r_squared > 0.7 and p_value and p_value < 0.05:
        if slope > 0:
            trend_type = TrendType.INCREASING
            evidence.append(f"Strong increasing trend (R²={r_squared:.2f}, p={p_value:.3f})")
        else:
            trend_type = TrendType.DECREASING
            evidence.append(f"Strong decreasing trend (R²={r_squared:.2f}, p={p_value:.3f})")
    elif r_squared > 0.4 and p_value and p_value < 0.1:
        if slope > 0:
            trend_type = TrendType.INCREASING
            evidence.append(f"Moderate increasing trend (R²={r_squared:.2f}, p={p_value:.3f})")
        else:
            trend_type = TrendType.DECREASING
            evidence.append(f"Moderate decreasing trend (R²={r_squared:.2f}, p={p_value:.3f})")
    elif abs(slope_per_month) < 0.01 * (abs(start_val) + abs(end_val)) / 2:
        trend_type = TrendType.STABLE
        evidence.append(f"Stable trend (slope={slope_per_month:.4f}/month)")
    else:
        # Check for fluctuation
        residuals = [yi - ypi for yi, ypi in zip(y, y_pred)]
        residual_std = statistics.stdev(residuals) if len(residuals) > 1 else 0
        value_range = max(y) - min(y)
        
        if residual_std > 0.3 * value_range and value_range > 0:
            trend_type = TrendType.FLUCTUATING
            evidence.append(f"Fluctuating pattern (residual std: {residual_std:.2f})")
        elif r_squared < 0.2:
            trend_type = TrendType.UNKNOWN
            evidence.append(f"No clear trend (R²={r_squared:.2f})")
        else:
            if slope > 0:
                trend_type = TrendType.INCREASING
            else:
                trend_type = TrendType.DECREASING
            evidence.append(f"Weak trend (R²={r_squared:.2f}, slope={slope_per_month:.4f}/month)")
    
    # Confidence based on R-squared, data points, and time span
    confidence = min(0.95, r_squared * 0.8 + min(len(points) / 20, 0.15) + min(time_span / 180, 0.05))
    
    return TrendResult(
        trend_type=trend_type,
        measurement_type=sorted_points[0].measurement_type,
        slope=slope,
        slope_per_month=slope_per_month,
        r_squared=r_squared,
        p_value=p_value,
        confidence=confidence,
        data_points=n,
        time_span_days=time_span,
        start_value=start_val,
        end_value=end_val,
        change_percent=change_percent,
        supporting_evidence=evidence,
        warnings=warnings
    )


def detect_cyclical_pattern(
    points: List[TimeSeriesPoint],
    min_period_days: float = 7,
    max_period_days: float = 90,
    min_points_per_cycle: int = 3
) -> Optional[CyclicalPattern]:
    """
    Detect cyclical patterns using autocorrelation.
    """
    if len(points) < 10:
        return None
    
    sorted_points = sorted(points, key=lambda p: p.timestamp)
    values = [p.value for p in sorted_points]
    timestamps = [p.timestamp for p in sorted_points]
    
    # Resample to regular intervals (daily)
    base_time = sorted_points[0].timestamp
    end_time = sorted_points[-1].timestamp
    total_days = (end_time - base_time).total_seconds() / 86400
    
    if total_days < max_period_days * 2:
        return None
    
    # Create daily series with interpolation
    daily_values = {}
    for p in sorted_points:
        day = int((p.timestamp - base_time).total_seconds() / 86400)
        daily_values[day] = p.value
    
    # Interpolate missing days
    series = []
    for day in range(int(total_days) + 1):
        if day in daily_values:
            series.append(daily_values[day])
        else:
            # Linear interpolation
            prev_day = max([d for d in daily_values if d <= day], default=None)
            next_day = min([d for d in daily_values if d >= day], default=None)
            if prev_day is not None and next_day is not None:
                weight = (day - prev_day) / (next_day - prev_day)
                series.append(daily_values[prev_day] * (1 - weight) + daily_values[next_day] * weight)
            elif prev_day is not None:
                series.append(daily_values[prev_day])
            elif next_day is not None:
                series.append(daily_values[next_day])
            else:
                series.append(0)
    
    # Autocorrelation for period detection
    max_lag = min(int(max_period_days), len(series) // 2)
    min_lag = int(min_period_days)
    
    best_lag = 0
    best_corr = 0
    
    mean_val = statistics.mean(series)
    var = statistics.variance(series) if len(series) > 1 else 1
    
    for lag in range(min_lag, max_lag + 1):
        if lag >= len(series):
            break
        corr = sum((series[i] - mean_val) * (series[i + lag] - mean_val) 
                   for i in range(len(series) - lag)) / (len(series) - lag) / var
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    
    if best_corr < 0.3:
        return None
    
    # Find peaks and troughs
    peaks = []
    troughs = []
    for i in range(1, len(series) - 1):
        if series[i] > series[i-1] and series[i] > series[i+1]:
            peaks.append(base_time + timedelta(days=i))
        elif series[i] < series[i-1] and series[i] < series[i+1]:
            troughs.append(base_time + timedelta(days=i))
    
    # Estimate amplitude
    if peaks and troughs:
        peak_vals = [series[int((p - base_time).total_seconds() / 86400)] for p in peaks]
        trough_vals = [series[int((t - base_time).total_seconds() / 86400)] for t in troughs]
        amplitude = (statistics.mean(peak_vals) - statistics.mean(trough_vals)) / 2
    else:
        amplitude = 0
    
    return CyclicalPattern(
        period_days=best_lag,
        amplitude=amplitude,
        phase_offset_days=0,  # Simplified
        confidence=best_corr,
        peaks=peaks,
        troughs=troughs
    )


def compare_trends(
    points_a: List[TimeSeriesPoint],
    points_b: List[TimeSeriesPoint],
    label_a: str = "A",
    label_b: str = "B"
) -> Dict[str, Any]:
    """
    Compare trends between two time series (e.g., before/after intervention).
    """
    trend_a = detect_trend(points_a)
    trend_b = detect_trend(points_b)
    
    result = {
        "trend_a": {
            "label": label_a,
            "type": trend_a.trend_type.value,
            "slope_per_month": trend_a.slope_per_month,
            "r_squared": trend_a.r_squared,
            "confidence": trend_a.confidence,
        },
        "trend_b": {
            "label": label_b,
            "type": trend_b.trend_type.value,
            "slope_per_month": trend_b.slope_per_month,
            "r_squared": trend_b.r_squared,
            "confidence": trend_b.confidence,
        },
        "comparison": {
            "slope_difference": trend_b.slope_per_month - trend_a.slope_per_month,
            "trend_changed": False,
            "change_description": "",
        }
    }
    
    # Determine if trend meaningfully changed
    slope_diff = abs(trend_b.slope_per_month - trend_a.slope_per_month)
    avg_slope = (abs(trend_a.slope_per_month) + abs(trend_b.slope_per_month)) / 2
    
    if slope_diff > 0.1 * max(avg_slope, 1):
        result["comparison"]["trend_changed"] = True
        if trend_a.trend_type != trend_b.trend_type:
            result["comparison"]["change_description"] = f"Trend changed from {trend_a.trend_type.value} to {trend_b.trend_type.value}"
        elif trend_b.slope_per_month > trend_a.slope_per_month:
            result["comparison"]["change_description"] = "Trend accelerated"
        else:
            result["comparison"]["change_description"] = "Trend decelerated"
    
    return result


def detect_multivariate_trend(
    series: Dict[str, List[TimeSeriesPoint]],
    min_correlation: float = 0.5
) -> Dict[str, Any]:
    """
    Detect correlated trends across multiple measurement types.
    """
    trends = {}
    for label, points in series.items():
        if len(points) >= 3:
            trends[label] = detect_trend(points)
    
    # Find correlated trends
    correlated_pairs = []
    labels = list(trends.keys())
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            trend_a = trends[a]
            trend_b = trends[b]
            
            # Check if slopes have same direction and similar magnitude
            if trend_a.slope * trend_b.slope > 0:  # same direction
                slope_ratio = abs(trend_a.slope_per_month / trend_b.slope_per_month) if trend_b.slope_per_month != 0 else float('inf')
                if 0.5 <= slope_ratio <= 2.0:
                    correlated_pairs.append({
                        "a": a, "b": b,
                        "correlation_type": "parallel",
                        "slope_a": trend_a.slope_per_month,
                        "slope_b": trend_b.slope_per_month,
                    })
    
    return {
        "individual_trends": {k: {
            "type": v.trend_type.value,
            "slope_per_month": v.slope_per_month,
            "confidence": v.confidence,
        } for k, v in trends.items()},
        "correlated_pairs": correlated_pairs,
    }


# Domain-specific trend interpretation
def interpret_clinical_trend(
    trend: TrendResult,
    measurement_type: MeasurementType,
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Provide domain-specific interpretation of trend.
    """
    interpretation = {
        "trend": trend.trend_type.value,
        "measurement_type": measurement_type.value,
        "clinical_significance": "unknown",
        "action_suggested": "monitor",
        "details": [],
    }
    
    context = context or {}
    
    # Vital signs
    if measurement_type == MeasurementType.VITAL_SIGN:
        if trend.trend_type == TrendType.INCREASING and trend.change_percent > 10:
            interpretation["clinical_significance"] = "potentially_concerning"
            interpretation["action_suggested"] = "clinical_review"
            interpretation["details"].append(f"Vital sign increased {trend.change_percent:.1f}% over {trend.time_span_days:.0f} days")
        elif trend.trend_type == TrendType.DECREASING and trend.change_percent < -10:
            interpretation["clinical_significance"] = "potentially_concerning"
            interpretation["action_suggested"] = "clinical_review"
            interpretation["details"].append(f"Vital sign decreased {abs(trend.change_percent):.1f}%")
    
    # Blood pressure
    elif measurement_type == MeasurementType.BLOOD_PRESSURE:
        if trend.trend_type == TrendType.INCREASING and trend.change_percent > 15:
            interpretation["clinical_significance"] = "concerning"
            interpretation["action_suggested"] = "medication_review"
            interpretation["details"].append("Blood pressure trending upward - consider medication review")
    
    # Weight
    elif measurement_type == MeasurementType.WEIGHT:
        if trend.trend_type == TrendType.INCREASING and trend.change_percent > 5:
            interpretation["clinical_significance"] = "monitor"
            interpretation["action_suggested"] = "dietary_review"
            interpretation["details"].append("Weight gain detected - review nutrition/fluid status")
        elif trend.trend_type == TrendType.DECREASING and trend.change_percent < -5:
            interpretation["clinical_significance"] = "concerning"
            interpretation["action_suggested"] = "nutritional_assessment"
            interpretation["details"].append("Weight loss detected - assess nutritional status")
    
    # Falls
    elif measurement_type == MeasurementType.FALL_COUNT:
        if trend.trend_type == TrendType.INCREASING:
            interpretation["clinical_significance"] = "concerning"
            interpretation["action_suggested"] = "falls_risk_assessment"
            interpretation["details"].append("Fall frequency increasing - urgent falls risk assessment needed")
    
    # Pain
    elif measurement_type == MeasurementType.PAIN_SCORE:
        if trend.trend_type == TrendType.INCREASING and trend.change_percent > 20:
            interpretation["clinical_significance"] = "concerning"
            interpretation["action_suggested"] = "pain_management_review"
            interpretation["details"].append("Pain scores worsening - review analgesia")
    
    # Cognitive
    elif measurement_type == MeasurementType.COGNITIVE_SCORE:
        if trend.trend_type == TrendType.DECREASING and trend.change_percent < -10:
            interpretation["clinical_significance"] = "concerning"
            interpretation["action_suggested"] = "cognitive_assessment"
            interpretation["details"].append("Cognitive scores declining - consider formal assessment")
    
    # Add general details
    interpretation["details"].append(f"Slope: {trend.slope_per_month:.3f}/month, R²: {trend.r_squared:.2f}")
    interpretation["details"].append(f"Confidence: {trend.confidence:.0%}")
    
    return interpretation


# Export
__all__ = [
    "TrendType",
    "MeasurementType",
    "TimeSeriesPoint",
    "TrendResult",
    "CyclicalPattern",
    "ensure_utc",
    "detect_trend",
    "detect_cyclical_pattern",
    "compare_trends",
    "detect_multivariate_trend",
    "interpret_clinical_trend",
]