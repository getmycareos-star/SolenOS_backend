"""
Longitudinal Understanding Service for detecting change points and analyzing trends in evolving care situations.
Builds upon existing solenos temporal services to provide comprehensive longitudinal analysis.
"""
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class LongitudinalUnderstandingService:
    """Service for detecting change points and analyzing trends in evolving care situations."""
    
    def __init__(self):
        """Initialize the Longitudinal Understanding service."""
        logger.info("Longitudinal Understanding service initialized")
    
    def detect_change_points(self, 
                           values: List[float], 
                           timestamps: List[datetime],
                           min_segment_size: int = 3,
                           penalty: float = 10.0) -> List[Dict[str, Any]]:
        """
        Detect change points in a time series using a simplified PELT algorithm.
        
        Args:
            values: List of measurement values
            timestamps: Corresponding timestamps
            min_segment_size: Minimum size of a segment
            penalty: Penalty for adding a change point (higher = fewer change points)
            
        Returns:
            List of change points with timestamps and statistical information
        """
        if len(values) < 2 * min_segment_size:
            return []
        
        # Sort by timestamp
        sorted_data = sorted(zip(timestamps, values), key=lambda x: x[0])
        sorted_timestamps, sorted_values = zip(*sorted_data)
        sorted_timestamps = list(sorted_timestamps)
        sorted_values = list(sorted_values)
        
        n = len(sorted_values)
        if n < 2:
            return []
        
        # Convert timestamps to days since first observation for easier calculation
        base_time = sorted_timestamps[0]
        days = [(ts - base_time).total_seconds() / 86400 for ts in sorted_timestamps]
        
        # Precompute cumulative sums for efficient cost calculation
        cumsum = [0] * (n + 1)
        cumsum2 = [0] * (n + 1)
        for i in range(n):
            cumsum[i + 1] = cumsum[i] + sorted_values[i]
            cumsum2[i + 1] = cumsum2[i] + sorted_values[i] ** 2
        
        # Cost function for segment [i, j) (using variance)
        def segment_cost(i: int, j: int) -> float:
            if j - i < min_segment_size:
                return float('inf')
            count = j - i
            sum_val = cumsum[j] - cumsum[i]
            sum_sq = cumsum2[j] - cumsum2[i]
            mean = sum_val / count
            # Variance = E[X²] - E[X]²
            variance = sum_sq / count - mean ** 2
            return count * variance  # Total squared error
        
        # Simplified PELT-like algorithm
        # Using binary segmentation for simplicity and efficiency
        change_points = []
        stack = [(0, n)]  # (start, end) indices
        
        while stack:
            start, end = stack.pop()
            if end - start < 2 * min_segment_size:
                continue
                
            # Find best change point in this segment
            best_cp = -1
            best_improvement = 0
            
            for cp in range(start + min_segment_size, end - min_segment_size + 1):
                cost_left = segment_cost(start, cp)
                cost_right = segment_cost(cp, end)
                cost_total = segment_cost(start, end)
                improvement = cost_total - (cost_left + cost_right) - penalty
                
                if improvement > best_improvement:
                    best_improvement = improvement
                    best_cp = cp
            
            if best_cp != -1:
                change_points.append({
                    "timestamp": sorted_timestamps[best_cp],
                    "day": days[best_cp],
                    "value_before": sorted_values[best_cp - 1],
                    "value_after": sorted_values[best_cp],
                    "improvement": best_improvement,
                    "segment_before": (start, best_cp),
                    "segment_after": (best_cp, end)
                })
                # Recursively check subsegments
                stack.append((start, best_cp))
                stack.append((best_cp, end))
        
        # Sort by timestamp
        change_points.sort(key=lambda x: x["timestamp"])
        return change_points
    
    def analyze_care_situation_evolution(self,
                                       evidence_snapshots: List[Dict[str, Any]],
                                       vital_signs_history: List[Dict[str, Any]] = None,
                                       symptom_history: List[Dict[str, Any]] = None,
                                       medication_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Analyze the evolution of a care situation over time using multiple data sources.
        
        Args:
            evidence_snapshots: List of evidence snapshots over time (from LongitudinalEvidenceService)
            vital_signs_history: List of vital sign measurements over time
            symptom_history: List of symptom assessments over time
            medication_history: List of medication changes over time
            
        Returns:
            Comprehensive longitudinal analysis of the care situation
        """
        analysis = {
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_evolution": {},
            "vital_signs_trends": {},
            "symptom_trends": {},
            "medication_changes": {},
            "change_points": {},
            "risk_trajectory": {},
            "care_trajectory": "stable",  # improving, deteriorating, stable, fluctuating
            "key_insights": [],
            "recommendations": []
        }
        
        try:
            # Analyze evidence evolution
            if evidence_snapshots and len(evidence_snapshots) >= 2:
                analysis["evidence_evolution"] = self._analyze_evidence_evolution(evidence_snapshots)
            
            # Analyze vital signs trends
            if vital_signs_history and len(vital_signs_history) >= 3:
                analysis["vital_signs_trends"] = self._analyze_vital_signs_trends(vital_signs_history)
            
            # Analyze symptom trends
            if symptom_history and len(symptom_history) >= 3:
                analysis["symptom_trends"] = self._analyze_symptom_trends(symptom_history)
            
            # Analyze medication changes
            if medication_history and len(medication_history) >= 2:
                analysis["medication_changes"] = self._analyze_medication_changes(medication_history)
            
            # Detect change points in key metrics
            analysis["change_points"] = self._detect_key_change_points(
                evidence_snapshots, vital_signs_history, symptom_history
            )
            
            # Assess overall risk trajectory
            analysis["risk_trajectory"] = self._assess_risk_trajectory(
                evidence_snapshots, vital_signs_history, symptom_history
            )
            
            # Determine overall care trajectory
            analysis["care_trajectory"] = self._determine_care_trajectory(analysis)
            
            # Generate key insights and recommendations
            analysis["key_insights"] = self._generate_key_insights(analysis)
            analysis["recommendations"] = self._generate_recommendations(analysis)
            
        except Exception as e:
            logger.error(f"Error in care situation evolution analysis: {e}")
            analysis["error"] = str(e)
        
        return analysis
    
    def _analyze_evidence_evolution(self, snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze how evidence characteristics evolve over time."""
        if len(snapshots) < 2:
            return {"trend": "insufficient_data"}
        
        # Sort by time
        sorted_snapshots = sorted(snapshots, key=lambda x: x.get("snapshot_time", ""))
        
        # Track key metrics over time
        total_evidence = []
        active_evidence = []
        confidence_scores = []
        evidence_types = {}
        
        for snap in sorted_snapshots:
            timestamp = snap.get("snapshot_time")
            total_evidence.append({
                "time": timestamp,
                "count": snap.get("total_evidence_count", 0)
            })
            active_evidence.append({
                "time": timestamp,
                "count": snap.get("active_evidence_count", 0)
            })
            if snap.get("avg_confidence_overall") is not None:
                confidence_scores.append({
                    "time": timestamp,
                    "value": snap.get("avg_confidence_overall")
                })
            
            # Track evidence type distribution
            for ev_type, count in snap.get("evidence_by_type", {}).items():
                if ev_type not in evidence_types:
                    evidence_types[ev_type] = []
                evidence_types[ev_type].append({
                    "time": timestamp,
                    "count": count
                })
        
        # Calculate trends
        evidence_trend = self._calculate_trend([(x["time"], x["count"]) for x in total_evidence])
        active_trend = self._calculate_trend([(x["time"], x["count"]) for x in active_evidence])
        confidence_trend = self._calculate_trend([(x["time"], x["value"]) for x in confidence_scores]) if confidence_scores else None
        
        return {
            "total_evidence_trend": evidence_trend,
            "active_evidence_trend": active_trend,
            "confidence_trend": confidence_trend,
            "evidence_type_trends": {
                ev_type: self._calculate_trend([(x["time"], x["count"]) for x in counts])
                for ev_type, counts in evidence_types.items()
            },
            "latest_snapshot": sorted_snapshots[-1],
            "snapshot_count": len(sorted_snapshots)
        }
    
    def _analyze_vital_signs_trends(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze trends in vital signs data."""
        # Group by vital sign type
        vital_signs = {
            "heart_rate": [],
            "blood_pressure_systolic": [],
            "blood_pressure_diastolic": [],
            "temperature": [],
            "oxygen_saturation": [],
            "respiratory_rate": []
        }
        
        for record in history:
            timestamp = record.get("timestamp") or record.get("time")
            if not timestamp:
                continue
                
            for vs_type in vital_signs.keys():
                if vs_type in record:
                    vital_signs[vs_type].append({
                        "time": timestamp,
                        "value": record[vs_type]
                    })
        
        # Analyze each vital sign
        trends = {}
        for vs_type, values in vital_signs.items():
            if len(values) >= 3:
                trend = self._calculate_trend([(x["time"], x["value"]) for x in values])
                trends[vs_type] = trend
                
                # Add clinical interpretation
                trends[vs_type]["clinical_interpretation"] = self._interpret_vital_sign_trend(
                    vs_type, trend
                )
        
        return trends
    
    def _analyze_symptom_trends(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze trends in symptom data."""
        # Similar structure to vital signs but for symptoms
        symptoms = {
            "pain_score": [],
            "fatigue_level": [],
            "anxiety_level": [],
            "depression_score": [],
            "mobility_score": [],
            "cognitive_score": []
        }
        
        for record in history:
            timestamp = record.get("timestamp") or record.get("time")
            if not timestamp:
                continue
                
            for symptom in symptoms.keys():
                if symptom in record:
                    symptoms[symptom].append({
                        "time": timestamp,
                        "value": record[symptom]
                    })
        
        trends = {}
        for symptom, values in symptoms.items():
            if len(values) >= 3:
                trend = self._calculate_trend([(x["time"], x["value"]) for x in values])
                trends[symptom] = trend
                trends[symptom]["clinical_interpretation"] = self._interpret_symptom_trend(
                    symptom, trend
                )
        
        return trends
    
    def _analyze_medication_changes(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze medication changes over time."""
        if len(history) < 2:
            return {"trend": "insufficient_data"}
        
        # Sort by time
        sorted_history = sorted(history, key=lambda x: x.get("time", ""))
        
        changes = []
        current_meds = set()
        
        for record in sorted_history:
            timestamp = record.get("time")
            meds = set(record.get("medications", []))
            
            added = meds - current_meds
            removed = current_meds - meds
            
            if added or removed:
                changes.append({
                    "time": timestamp,
                    "added": list(added),
                    "removed": list(removed),
                    "total_medications": len(meds)
                })
            
            current_meds = meds
        
        # Calculate medication burden trend
        med_counts = [{"time": r.get("time"), "count": len(r.get("medications", []))} 
                     for r in sorted_history if "medications" in r]
        burden_trend = self._calculate_trend([(x["time"], x["count"]) for x in med_counts]) if med_counts else None
        
        return {
            "medication_changes": changes,
            "medication_burden_trend": burden_trend,
            "total_changes": len(changes),
            "current_medications": list(current_meds) if sorted_history else []
        }
    
    def _detect_key_change_points(self,
                                evidence_snapshots: List[Dict[str, Any]],
                                vital_signs_history: List[Dict[str, Any]] = None,
                                symptom_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Detect significant change points in key care metrics."""
        change_points = {}
        
        # Check evidence total count for change points
        if evidence_snapshots and len(evidence_snapshots) >= 6:
            values = [s.get("total_evidence_count", 0) for s in evidence_snapshots]
            timestamps = [datetime.fromisoformat(s["snapshot_time"].replace("Z", "+00:00")) 
                         for s in evidence_snapshots if s.get("snapshot_time")]
            if len(timestamps) == len(values):
                cps = self.detect_change_points(values, timestamps)
                change_points["total_evidence_count"] = cps
        
        # Check confidence for change points
        if evidence_snapshots and len(evidence_snapshots) >= 6:
            values = [s.get("avg_confidence_overall", 0) for s in evidence_snapshots 
                     if s.get("avg_confidence_overall") is not None]
            timestamps = [datetime.fromisoformat(s["snapshot_time"].replace("Z", "+00:00")) 
                         for s in evidence_snapshots if s.get("snapshot_time") and s.get("avg_confidence_overall") is not None]
            if len(timestamps) == len(values) and len(values) >= 6:
                cps = self.detect_change_points(values, timestamps)
                change_points["confidence_overall"] = cps
        
        # Check vital signs for change points
        if vital_signs_history:
            for vs_type in ["heart_rate", "blood_pressure_systolic", "temperature"]:
                values = [r.get(vs_type) for r in vital_signs_history if r.get(vs_type) is not None]
                timestamps = [datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) 
                             for r in vital_signs_history if r.get("timestamp") and r.get(vs_type) is not None]
                if len(timestamps) == len(values) and len(values) >= 6:
                    cps = self.detect_change_points(values, timestamps)
                    change_points[f"vital_sign_{vs_type}"] = cps
        
        return change_points
    
    def _assess_risk_trajectory(self,
                              evidence_snapshots: List[Dict[str, Any]],
                              vital_signs_history: List[Dict[str, Any]] = None,
                              symptom_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Assess the overall risk trajectory based on multiple indicators."""
        risk_factors = []
        protective_factors = []
        
        # From evidence snapshots
        if evidence_snapshots and len(evidence_snapshots) >= 2:
            latest = evidence_snapshots[-1]
            earliest = evidence_snapshots[0]
            
            # Check for increasing evidence volume (could indicate complexity)
            earliest_count = earliest.get("total_evidence_count", 0)
            latest_count = latest.get("total_evidence_count", 0)
            if latest_count > earliest_count * 1.5:
                risk_factors.append({
                    "factor": "Increasing evidence volume",
                    "change": f"{earliest_count} → {latest_count} evidence items",
                    "impact": "medium"
                })
            
            # Check for decreasing confidence
            earliest_conf = earliest.get("avg_confidence_overall")
            latest_conf = latest.get("avg_confidence_overall")
            if earliest_conf is not None and latest_conf is not None:
                if latest_conf < earliest_conf * 0.8:
                    risk_factors.append({
                        "factor": "Decreasing confidence in evidence",
                        "change": f"{earliest_conf:.2f} → {latest_conf:.2f}",
                        "impact": "medium"
                    })
        
        # From vital signs
        if vital_signs_history and len(vital_signs_history) >= 3:
            # Check for deteriorating vital signs
            earliest_vs = vital_signs_history[0]
            latest_vs = vital_signs_history[-1]
            
            # Heart rate trends
            if "heart_rate" in earliest_vs and "heart_rate" in latest_vs:
                hr_change = latest_vs["heart_rate"] - earliest_vs["heart_rate"]
                if hr_change > 15:  # Significantly elevated
                    risk_factors.append({
                        "factor": "Tachycardia developing",
                        "change": f"+{hr_change:.1f} bpm",
                        "impact": "high"
                    })
                elif hr_change < -15:  # Significantly low
                    risk_factors.append({
                        "factor": "Bradycardia developing",
                        "change": f"{hr_change:.1f} bpm",
                        "impact": "medium"
                    })
            
            # Blood pressure trends
            if "blood_pressure_systolic" in earliest_vs and "blood_pressure_systolic" in latest_vs:
                bp_change = latest_vs["blood_pressure_systolic"] - earliest_vs["blood_pressure_systolic"]
                if bp_change > 20:  # Significantly elevated
                    risk_factors.append({
                        "factor": "Hypertension developing",
                        "change": f"+{bp_change:.1f} mmHg systolic",
                        "impact": "high"
                    })
        
        # From symptoms
        if symptom_history and len(symptom_history) >= 3:
            earliest_symp = symptom_history[0]
            latest_symp = symptom_history[-1]
            
            # Pain increase
            if "pain_score" in earliest_symp and "pain_score" in latest_symp:
                pain_change = latest_symp["pain_score"] - earliest_symp["pain_score"]
                if pain_change > 2:  # Significant increase
                    risk_factors.append({
                        "factor": "Worsening pain",
                        "change": f"+{pain_change:.1f} points",
                        "impact": "medium"
                    })
            
            # Cognitive decline
            if "cognitive_score" in earliest_symp and "cognitive_score" in latest_symp:
                cog_change = latest_symp["cognitive_score"] - earliest_symp["cognitive_score"]
                if cog_change < -2:  # Significant decline
                    risk_factors.append({
                        "factor": "Cognitive decline",
                        "change": f"{cog_change:.1f} points",
                        "impact": "high"
                    })
        
        # Protective factors (improving trends)
        # ... (similar logic for improving conditions)
        
        # Calculate overall risk level
        high_risk = sum(1 for rf in risk_factors if rf.get("impact") == "high")
        med_risk = sum(1 for rf in risk_factors if rf.get("impact") == "medium")
        
        if high_risk > 0:
            overall_risk = "high"
        elif med_risk > 1:
            overall_risk = "medium"
        elif len(risk_factors) > 0:
            overall_risk = "low"
        else:
            overall_risk = "minimal"
        
        return {
            "risk_level": overall_risk,
            "risk_factors": risk_factors,
            "protective_factors": protective_factors,
            "assessment_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def _determine_care_trajectory(self, analysis: Dict[str, Any]) -> str:
        """Determine the overall care trajectory based on all analyses."""
        # Check for clear deterioration patterns
        deterioration_indicators = 0
        improvement_indicators = 0
        
        # From evidence evolution
        evidence_evo = analysis.get("evidence_evolution", {})
        if evidence_evo.get("confidence_trend"):
            conf_trend = evidence_evo["confidence_trend"]
            if conf_trend.get("slope_per_month", 0) < -0.02:  # Decreasing confidence
                deterioration_indicators += 1
            elif conf_trend.get("slope_per_month", 0) > 0.02:  # Increasing confidence
                improvement_indicators += 1
        
        # From vital signs
        vital_trends = analysis.get("vital_signs_trends", {})
        for vs_type, trend in vital_trends.items():
            if isinstance(trend, dict) and "slope_per_month" in trend:
                slope = trend["slope_per_month"]
                # For most vital signs, increasing is bad (HR, BP, temp)
                if vs_type in ["heart_rate", "blood_pressure_systolic", "temperature"] and slope > 0.5:
                    deterioration_indicators += 1
                elif vs_type in ["oxygen_saturation"] and slope < -0.5:  # Decreasing SpO2 is bad
                    deterioration_indicators += 1
                elif vs_type in ["heart_rate", "blood_pressure_systolic", "temperature"] and slope < -0.5:
                    improvement_indicators += 1
                elif vs_type in ["oxygen_saturation"] and slope > 0.5:
                    improvement_indicators += 1
        
        # From symptom trends
        symptom_trends = analysis.get("symptom_trends", {})
        for symptom, trend in symptom_trends.items():
            if isinstance(trend, dict) and "slope_per_month" in trend:
                slope = trend["slope_per_month"]
                # For symptoms, increasing is usually bad (pain, fatigue, etc.)
                if symptom in ["pain_score", "fatigue_level", "anxiety_level", "depression_score"] and slope > 0.3:
                    deterioration_indicators += 1
                elif symptom in ["mobility_score", "cognitive_score"] and slope < -0.3:  # Decreasing ability is bad
                    deterioration_indicators += 1
                elif symptom in ["pain_score", "fatigue_level", "anxiety_level", "depression_score"] and slope < -0.3:
                    improvement_indicators += 1
                elif symptom in ["mobility_score", "cognitive_score"] and slope > 0.3:
                    improvement_indicators += 1
        
        # Determine trajectory
        if deterioration_indicators > improvement_indicators + 1:
            return "deteriorating"
        elif improvement_indicators > deterioration_indicators + 1:
            return "improving"
        elif deterioration_indicators > 0 or improvement_indicators > 0:
            return "fluctuating"
        else:
            return "stable"
    
    def _calculate_trend(self, data_points: List[Tuple[datetime, float]]) -> Dict[str, Any]:
        """Calculate linear trend for a series of timestamp-value pairs."""
        if len(data_points) < 3:
            return {"trend": "insufficient_data", "slope": 0, "r_squared": 0}
        
        # Sort by time
        sorted_data = sorted(data_points, key=lambda x: x[0])
        times, values = zip(*sorted_data)
        
        # Convert to numpy-like operations
        n = len(times)
        base_time = times[0]
        x_vals = [(t - base_time).total_seconds() / 86400 for t in times]  # days
        y_vals = list(values)
        
        # Calculate linear regression
        sum_x = sum(x_vals)
        sum_y = sum(y_vals)
        sum_xy = sum(x * y for x, y in zip(x_vals, y_vals))
        sum_x2 = sum(x * x for x in x_vals)
        
        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return {"trend": "undefined", "slope": 0, "r_squared": 0}
        
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n
        
        # Calculate R-squared
        y_mean = sum_y / n
        ss_tot = sum((y - y_mean) ** 2 for y in y_vals)
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_vals, y_vals))
        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        
        # Calculate change over the period
        if len(x_vals) >= 2:
            start_val = y_vals[0]
            end_val = y_vals[-1]
            change_percent = ((end_val - start_val) / abs(start_val) * 100) if start_val != 0 else 0
            time_span = x_vals[-1] - x_vals[0]
        else:
            start_val = end_val = change_percent = time_span = 0
        
        # Determine trend direction and strength
        if abs(slope) < 0.001:  # Essentially flat
            trend_dir = "stable"
        elif slope > 0:
            trend_dir = "increasing"
        else:
            trend_dir = "decreasing"
        
        # Strength based on R-squared
        if r_squared > 0.7:
            strength = "strong"
        elif r_squared > 0.4:
            strength = "moderate"
        elif r_squared > 0.2:
            strength = "weak"
        else:
            strength = "very_weak"
        
        return {
            "trend": f"{trend_dir}_{strength}",
            "slope": slope,  # per day
            "slope_per_month": slope * 30.44,
            "intercept": intercept,
            "r_squared": r_squared,
            "start_value": start_val,
            "end_value": end_val,
            "change_percent": change_percent,
            "time_span_days": time_span,
            "data_points": n
        }
    
    def _interpret_vital_sign_trend(self, vs_type: str, trend: Dict[str, Any]) -> Dict[str, Any]:
        """Provide clinical interpretation of vital sign trends."""
        interpretation = {
            "trend": trend.get("trend", "unknown"),
            "clinical_significance": "unknown",
            "monitoring_recommendation": "routine"
        }
        
        slope_per_month = trend.get("slope_per_month", 0)
        change_percent = trend.get("change_percent", 0)
        
        if vs_type == "heart_rate":
            if slope_per_month > 2:  # Increasing by >2 bpm/month
                interpretation["clinical_significance"] = "developing_tachycardia"
                interpretation["monitoring_recommendation"] = "weekly_review"
            elif slope_per_month < -2:  # Decreasing by >2 bpm/month
                interpretation["clinical_significance"] = "improving_heart_rate"
                interpretation["monitoring_recommendation"] = "routine"
        
        elif vs_type == "blood_pressure_systolic":
            if slope_per_month > 1.5:  # Increasing by >1.5 mmHg/month
                interpretation["clinical_significance"] = "developing_hypertension"
                interpretation["monitoring_recommendation"] = "biweekly_review"
            elif slope_per_month < -1.5:  # Decreasing by >1.5 mmHg/month
                interpretation["clinical_significance"] = "improving_blood_pressure"
                interpretation["monitoring_recommendation"] = "routine"
        
        elif vs_type == "temperature":
            if slope_per_month > 0.1:  # Increasing by >0.1°C/month
                interpretation["clinical_significance"] = "developing_fever"
                interpretation["monitoring_recommendation"] = "clinical_review"
            elif slope_per_month < -0.1:  # Decreasing by >0.1°C/month
                interpretation["clinical_significance"] = "resolving_fever"
                interpretation["monitoring_recommendation"] = "routine"
        
        elif vs_type == "oxygen_saturation":
            if slope_per_month < -0.5:  # Decreasing by >0.5%/month
                interpretation["clinical_significance"] = "worsening_oxygenation"
                interpretation["monitoring_recommendation"] = "urgent_review"
            elif slope_per_month > 0.5:  # Increasing by >0.5%/month
                interpretation["clinical_significance"] = "improving_oxygenation"
                interpretation["monitoring_recommendation"] = "routine"
        
        return interpretation
    
    def _interpret_symptom_trend(self, symptom: str, trend: Dict[str, Any]) -> Dict[str, Any]:
        """Provide clinical interpretation of symptom trends."""
        interpretation = {
            "trend": trend.get("trend", "unknown"),
            "clinical_significance": "unknown",
            "monitoring_recommendation": "routine"
        }
        
        slope_per_month = trend.get("slope_per_month", 0)
        change_percent = trend.get("change_percent", 0)
        
        if symptom == "pain_score":
            if slope_per_month > 0.5:  # Increasing pain
                interpretation["clinical_significance"] = "worsening_pain"
                interpretation["monitoring_recommendation"] = "pain_management_review"
            elif slope_per_month < -0.5:  # Decreasing pain
                interpretation["clinical_significance"] = "improving_pain_control"
                interpretation["monitoring_recommendation"] = "routine"
        
        elif symptom == "fatigue_level":
            if slope_per_month > 0.3:  # Increasing fatigue
                interpretation["clinical_significance"] = "increasing_fatigue"
                interpretation["monitoring_recommendation"] = "fatigue_assessment"
            elif slope_per_month < -0.3:  # Decreasing fatigue
                interpretation["clinical_significance"] = "decreasing_fatigue"
                interpretation["monitoring_recommendation"] = "routine"
        
        elif symptom == "cognitive_score":
            if slope_per_month < -0.2:  # Decreasing cognition
                interpretation["clinical_significance"] = "cognitive_decline"
                interpretation["monitoring_recommendation"] = "cognitive_assessment"
            elif slope_per_month > 0.2:  # Increasing cognition
                interpretation["clinical_significance"] = "cognitive_improvement"
                interpretation["monitoring_recommendation"] = "routine"
        
        elif symptom == "mobility_score":
            if slope_per_month < -0.2:  # Decreasing mobility
                interpretation["clinical_significance"] = "mobility_decline"
                interpretation["monitoring_recommendation"] = "mobility_assessment"
            elif slope_per_month > 0.2:  # Increasing mobility
                interpretation["clinical_significance"] = "mobility_improvement"
                interpretation["monitoring_recommendation"] = "routine"
        
        return interpretation
    
    def _generate_key_insights(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate key insights from the longitudinal analysis."""
        insights = []
        
        # Evidence evolution insights
        evidence_evo = analysis.get("evidence_evolution", {})
        if evidence_evo.get("confidence_trend"):
            conf_trend = evidence_evo["confidence_trend"]
            if conf_trend.get("slope_per_month", 0) < -0.01:
                insights.append("Confidence in evidence documentation is declining over time")
            elif conf_trend.get("slope_per_month", 0) > 0.01:
                insights.append("Confidence in evidence documentation is improving over time")
        
        # Vital signs insights
        vital_trends = analysis.get("vital_signs_trends", {})
        for vs_type, trend in vital_trends.items():
            if isinstance(trend, dict) and "trend" in trend:
                if "increasing" in trend["trend"] and vs_type in ["heart_rate", "blood_pressure_systolic"]:
                    insights.append(f"{vs_type.replace('_', ' ').title()} shows increasing trend")
                elif "decreasing" in trend["trend"] and vs_type == "oxygen_saturation":
                    insights.append("Oxygen saturation shows decreasing trend - monitor for hypoxemia")
        
        # Symptom insights
        symptom_trends = analysis.get("symptom_trends", {})
        for symptom, trend in symptom_trends.items():
            if isinstance(trend, dict) and "trend" in trend:
                if "increasing" in trend["trend"] and symptom in ["pain_score", "fatigue_level"]:
                    insights.append(f"{symptom.replace('_', ' ').title()} is worsening over time")
                elif "decreasing" in trend["trend"] and symptom == "cognitive_score":
                    insights.append("Cognitive function shows declining trend")
        
        # Change points insights
        change_points = analysis.get("change_points", {})
        total_cps = sum(len(cps) if isinstance(cps, list) else 0 
                       for cps in change_points.values())
        if total_cps > 0:
            insights.append(f"Detected {total_cps} significant change points in care metrics")
        
        # Risk trajectory insights
        risk_traj = analysis.get("risk_trajectory", {})
        risk_level = risk_traj.get("risk_level", "unknown")
        if risk_level == "high":
            insights.append("Overall risk assessment indicates high risk situation")
        elif risk_level == "medium":
            insights.append("Overall risk assessment indicates moderate risk situation")
        
        if not insights:
            insights.append("No significant longitudinal patterns detected in available data")
        
        return insights
    
    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on the longitudinal analysis."""
        recommendations = []
        
        # Based on care trajectory
        trajectory = analysis.get("care_trajectory", "stable")
        if trajectory == "deteriorating":
            recommendations.append("Consider comprehensive care plan review - situation appears to be deteriorating")
            recommendations.append("Increase monitoring frequency and consider escalation of care")
        elif trajectory == "improving":
            recommendations.append("Continue current care approach - situation shows improvement")
            recommendations.append("Consider gradual reduction of interventions if appropriate")
        elif trajectory == "fluctuating":
            recommendations.append("Monitor closely for patterns - situation shows fluctuation")
            recommendations.append("Consider identifying triggers for fluctuations")
        else:
            recommendations.append("Routine monitoring appropriate - situation appears stable")
        
        # Based on risk trajectory
        risk_traj = analysis.get("risk_trajectory", {})
        risk_level = risk_traj.get("risk_level", "low")
        if risk_level == "high":
            recommendations.append("Urgent clinical review recommended due to high risk factors")
            recommendations.append("Consider implementing additional monitoring or interventions")
        elif risk_level == "medium":
            recommendations.append("Increase monitoring frequency due to moderate risk factors")
        
        # Based on specific trends
        vital_trends = analysis.get("vital_signs_trends", {})
        for vs_type, trend in vital_trends.items():
            if isinstance(trend, dict):
                if vs_type == "heart_rate" and trend.get("trend", "").startswith("increasing"):
                    recommendations.append("Monitor heart rate - consider cardiac evaluation if persistent")
                elif vs_type == "blood_pressure_systolic" and trend.get("trend", "").startswith("increasing"):
                    recommendations.append("Monitor blood pressure - consider antihypertensive review")
                elif vs_type == "oxygen_saturation" and trend.get("trend", "").startswith("decreasing"):
                    recommendations.append("Monitor oxygen saturation closely - consider pulmonary evaluation")
        
        symptom_trends = analysis.get("symptom_trends", {})
        for symptom, trend in symptom_trends.items():
            if isinstance(trend, dict):
                if symptom == "pain_score" and trend.get("trend", "").startswith("increasing"):
                    recommendations.append("Review pain management regimen")
                elif symptom == "cognitive_score" and trend.get("trend", "").startswith("decreasing"):
                    recommendations.append("Consider formal cognitive assessment")
        
        # Based on change points
        change_points = analysis.get("change_points", {})
        total_cps = sum(len(cps) if isinstance(cps, list) else 0 
                       for cps in change_points.values())
        if total_cps > 3:
            recommendations.append("Multiple change points detected - consider investigating underlying causes")
        
        # Limit recommendations
        if len(recommendations) > 5:
            recommendations = recommendations[:5]
        
        if not recommendations:
            recommendations.append("Continue current monitoring schedule")
        
        return recommendations

# Global instance for reuse
_longitudinal_service = None

def get_longitudinal_service() -> LongitudinalUnderstandingService:
    """Get or create the global Longitudinal Understanding service instance."""
    global _longitudinal_service
    if _longitudinal_service is None:
        _longitudinal_service = LongitudinalUnderstandingService()
    return _longitudinal_service

def analyze_longitudinal_care_situation(evidence_snapshots: List[Dict[str, Any]] = None,
                                       vital_signs_history: List[Dict[str, Any]] = None,
                                       symptom_history: List[Dict[str, Any]] = None,
                                       medication_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Convenience function to analyze longitudinal care situation.
    
    Args:
        evidence_snapshots: List of evidence snapshots over time
        vital_signs_history: List of vital sign measurements over time
        symptom_history: List of symptom assessments over time
        medication_history: List of medication changes over time
        
    Returns:
        Comprehensive longitudinal analysis
    """
    service = get_longitudinal_service()
    return service.analyze_care_situation_evolution(
        evidence_snapshots, vital_signs_history, symptom_history, medication_history
    )