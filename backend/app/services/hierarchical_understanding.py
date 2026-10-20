"""
Hierarchical Understanding Service for multi-scale reasoning from specific observations to general care principles.
Enables reasoning across different levels of abstraction and granularity in care situations.
"""
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Set, Union
from enum import Enum
import json

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)

class ScaleLevel(Enum):
    """Levels of scale in hierarchical understanding."""
    OBSERVATION = "observation"        # Specific, granular data points
    EVENT = "event"                    # Individual care events or incidents
    EPISODE = "episode"                # Clusters of related events over time
    CONDITION = "condition"            # Medical conditions or health states
    POPULATION = "population"          # Groups or cohorts of patients
    PRINCIPLE = "principle"            # General care principles, guidelines, best practices

class HierarchicalUnderstandingService:
    """Service for multi-scale reasoning from specific observations to general care principles."""
    
    def __init__(self):
        """Initialize the Hierarchical Understanding service."""
        self._init_scale_mappings()
        logger.info("Hierarchical Understanding service initialized")
    
    def _init_scale_mappings(self):
        """Initialize mappings between different scales of care understanding."""
        # Define how observations aggregate to higher scales
        self.scale_hierarchy = [
            ScaleLevel.OBSERVATION,
            ScaleLevel.EVENT,
            ScaleLevel.EPISODE,
            ScaleLevel.CONDITION,
            ScaleLevel.POPULATION,
            ScaleLevel.PRINCIPLE
        ]
        
        # Define typical aggregation functions for each scale transition
        self.aggregation_functions = {
            (ScaleLevel.OBSERVATION, ScaleLevel.EVENT): "temporal_clustering",
            (ScaleLevel.EVENT, ScaleLevel.EPISODE): "thematic_grouping",
            (ScaleLevel.EPISODE, ScaleLevel.CONDITION): "pattern_recognition",
            (ScaleLevel.CONDITION, ScaleLevel.POPULATION): "statistical_summarization",
            (ScaleLevel.POPULATION, ScaleLevel.PRINCIPLE): "generalization_abstraction"
        }
        
        # Define example principles that emerge at the highest scale
        self.care_principles = {
            "prevention": "Preventing harm is preferable to treating it",
            "patient_centered": "Care should be respectful of and responsive to individual patient preferences",
            "evidence_based": "Clinical decisions should be informed by best available evidence",
            "safety_first": "Avoiding harm is a primary consideration in care delivery",
            "continuity": "Care should be coordinated and continuous across time and providers",
            "access": "Timely access to care should be equitable for all patients",
            "efficiency": "Resources should be used wisely to achieve optimal health outcomes",
            "equity": "Care should be provided fairly without unnecessary disparities"
        }
    
    def analyze_hierarchical_structure(self, 
                                     observations: List[Dict[str, Any]],
                                     time_window_hours: int = 24) -> Dict[str, Any]:
        """
        Analyze observations across multiple scales of understanding.
        
        Args:
            observations: List of specific observations (timestamped data points)
            time_window_hours: Time window for analysis in hours
            
        Returns:
            Multi-scale analysis from observations to principles
        """
        if not observations:
            return self._get_default_result()
        
        try:
            # Sort observations by timestamp
            sorted_obs = sorted(observations, key=lambda x: x.get("timestamp", datetime.min.replace(tzinfo=timezone.utc)))
            
            # Scale 1: Observations (input)
            observation_analysis = self._analyze_observations(sorted_obs)
            
            # Scale 2: Events (clustered observations)
            events = self._cluster_observations_into_events(sorted_obs, time_window_hours)
            event_analysis = self._analyze_events(events)
            
            # Scale 3: Episodes (themed event groups)
            episodes = self._group_events_into_episodes(events)
            episode_analysis = self._analyze_episodes(episodes)
            
            # Scale 4: Conditions (recognized patterns)
            conditions = self._recognize_patterns_in_episodes(episodes)
            condition_analysis = self._analyze_conditions(conditions)
            
            # Scale 5: Populations (condition summaries)
            populations = self._summarize_conditions(conditions)
            population_analysis = self._analyze_populations(populations)
            
            # Scale 6: Principles (generalized insights)
            principles = self._derive_principles_from_populations(populations)
            principle_analysis = self._analyze_principles(principles)
            
            # Generate cross-scale insights
            cross_scale_insights = self._generate_cross_scale_insights(
                observation_analysis, event_analysis, episode_analysis,
                condition_analysis, population_analysis, principle_analysis
            )
            
            # Assess hierarchical coherence
            coherence_score = self._assess_hierarchical_coherence(
                observation_analysis, event_analysis, episode_analysis,
                condition_analysis, population_analysis, principle_analysis
            )
            
            return {
                "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
                "time_window_hours": time_window_hours,
                "input_observation_count": len(observations),
                "scales": {
                    "observation": observation_analysis,
                    "event": event_analysis,
                    "episode": episode_analysis,
                    "condition": condition_analysis,
                    "population": population_analysis,
                    "principle": principle_analysis
                },
                "cross_scale_insights": cross_scale_insights,
                "hierarchical_coherence_score": coherence_score,
                "abstraction_level": self._determine_abstraction_level(
                    observation_analysis, event_analysis, episode_analysis,
                    condition_analysis, population_analysis, principle_analysis
                )
            }
            
        except Exception as e:
            logger.error(f"Error in hierarchical analysis: {e}")
            return self._get_default_error_result(str(e))
    
    def _analyze_observations(self, observations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze raw observations at the most granular scale."""
        if not observations:
            return {"count": 0, "quality": "no_data"}
        
        # Basic observation statistics
        values = []
        timestamps = []
        for obs in observations:
            if "value" in obs and obs["value"] is not None:
                try:
                    values.append(float(obs["value"]))
                except (ValueError, TypeError):
                    pass
            if "timestamp" in obs:
                try:
                    ts = obs["timestamp"]
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    timestamps.append(ts)
                except:
                    pass
        
        analysis = {
            "count": len(observations),
            "has_numeric_data": len(values) > 0,
            "has_timestamps": len(timestamps) > 0,
            "time_span_hours": 0,
            "value_statistics": {}
        }
        
        if len(timestamps) >= 2:
            time_span = (max(timestamps) - min(timestamps)).total_seconds() / 3600
            analysis["time_span_hours"] = time_span
        
        if values:
            analysis["value_statistics"] = {
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "count": len(values),
                "range": max(values) - min(values) if len(values) > 1 else 0
            }
            
            if NUMPY_AVAILABLE and len(values) > 1:
                analysis["value_statistics"]["std"] = float(np.std(values))
                analysis["value_statistics"]["median"] = float(np.median(values))
        
        analysis["quality"] = "good" if len(observations) > 5 else "limited" if len(observations) > 0 else "none"
        
        return analysis
    
    def _cluster_observations_into_events(self, 
                                        observations: List[Dict[str, Any]],
                                        time_window_hours: int) -> List[Dict[str, Any]]:
        """Cluster observations into events based on temporal proximity."""
        if not observations:
            return []
        
        # Sort by timestamp
        sorted_obs = sorted(observations, key=lambda x: x.get("timestamp", datetime.min.replace(tzinfo=timezone.utc)))
        
        events = []
        current_event = {
            "observations": [sorted_obs[0]],
            "start_time": sorted_obs[0].get("timestamp"),
            "end_time": sorted_obs[0].get("timestamp"),
            "observation_count": 1
        }
        
        window_seconds = time_window_hours * 3600
        
        for obs in sorted_obs[1:]:
            obs_time = obs.get("timestamp")
            if isinstance(obs_time, str):
                try:
                    obs_time = datetime.fromisoformat(obs_time.replace("Z", "+00:00"))
                except:
                    obs_time = None
            
            last_time = current_event["end_time"]
            if isinstance(last_time, str):
                try:
                    last_time = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
                except:
                    last_time = None
            
            # If within time window, add to current event
            if obs_time and last_time:
                try:
                    time_diff = (obs_time - last_time).total_seconds()
                    if time_diff <= window_seconds:
                        current_event["observations"].append(obs)
                        current_event["end_time"] = obs_time
                        current_event["observation_count"] += 1
                        continue
                except:
                    pass
            
            # Otherwise, finalize current event and start new one
            if current_event["observation_count"] > 0:
                events.append(current_event)
            
            current_event = {
                "observations": [obs],
                "start_time": obs_time,
                "end_time": obs_time,
                "observation_count": 1
            }
        
        # Don't forget the last event
        if current_event["observation_count"] > 0:
            events.append(current_event)
        
        return events
    
    def _analyze_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze events at the intermediate scale."""
        if not events:
            return {"count": 0, "quality": "no_data"}
        
        # Event statistics
        observation_counts = [e.get("observation_count", 0) for e in events]
        durations = []
        
        for event in events:
            start_time = event.get("start_time")
            end_time = event.get("end_time")
            if start_time and end_time:
                try:
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    if isinstance(end_time, str):
                        end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    duration = (end_time - start_time).total_seconds() / 3600  # hours
                    if duration >= 0:
                        durations.append(duration)
                except:
                    pass
        
        analysis = {
            "count": len(events),
            "avg_observations_per_event": sum(observation_counts) / len(observation_counts) if observation_counts else 0,
            "avg_duration_hours": sum(durations) / len(durations) if durations else 0,
            "duration_range_hours": [min(durations), max(durations)] if durations else [0, 0],
            "event_density": len(events) / max((max([e.get("end_time") for e in events if e.get("end_time")]) - 
                                              min([e.get("start_time") for e in events if e.get("start_time")])).total_seconds() / 3600, 1) if len(events) > 1 and durations else 0
        }
        
        analysis["quality"] = "good" if len(events) > 3 else "limited" if len(events) > 0 else "none"
        
        return analysis
    
    def _group_events_into_episodes(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group events into episodes based on thematic similarity."""
        # Simplified grouping - in practice this would use more sophisticated similarity measures
        if not events:
            return []
        
        # Group by observation type or similar characteristics
        episodes = []
        current_episode = {
            "events": [events[0]],
            "start_time": events[0].get("start_time"),
            "end_time": events[0].get("end_time"),
            "event_count": 1,
            "theme": "general"  # Would be determined by content analysis
        }
        
        for event in events[1:]:
            # Simple grouping rule: if events are close in time, group them
            # In practice, this would use semantic similarity of event content
            last_event = current_episode["events"][-1]
            last_time = last_event.get("end_time")
            curr_time = event.get("start_time")
            
            if last_time and curr_time:
                try:
                    if isinstance(last_time, str):
                        last_time = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
                    if isinstance(curr_time, str):
                        curr_time = datetime.fromisoformat(curr_time.replace("Z", "+00:00"))
                    
                    # Group if within 6 hours (configurable)
                    if (curr_time - last_time).total_seconds() <= 6 * 3600:
                        current_episode["events"].append(event)
                        current_episode["end_time"] = curr_time
                        current_episode["event_count"] += 1
                        continue
                except:
                    pass
            
            # Finalize current episode and start new one
            if current_episode["event_count"] > 0:
                episodes.append(current_episode)
            
            current_episode = {
                "events": [event],
                "start_time": event.get("start_time"),
                "end_time": event.get("end_time"),
                "event_count": 1,
                "theme": "general"
            }
        
        # Don't forget the last episode
        if current_episode["event_count"] > 0:
            episodes.append(current_episode)
        
        return episodes
    
    def _analyze_episodes(self, episodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze episodes at the pattern scale."""
        if not episodes:
            return {"count": 0, "quality": "no_data"}
        
        event_counts = [e.get("event_count", 0) for e in episodes]
        durations = []
        
        for episode in episodes:
            start_time = episode.get("start_time")
            end_time = episode.get("end_time")
            if start_time and end_time:
                try:
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    if isinstance(end_time, str):
                        end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    duration = (end_time - start_time).total_seconds() / 3600  # hours
                    if duration >= 0:
                        durations.append(duration)
                except:
                    pass
        
        analysis = {
            "count": len(episodes),
            "avg_events_per_episode": sum(event_counts) / len(event_counts) if event_counts else 0,
            "avg_duration_hours": sum(durations) / len(durations) if durations else 0,
            "duration_range_hours": [min(durations), max(durations)] if durations else [0, 0],
            "episode_density": len(episodes) / max((max([e.get("end_time") for e in episodes if e.get("end_time")]) - 
                                                  min([e.get("start_time") for e in episodes if e.get("start_time")])).total_seconds() / 3600, 1) if len(episodes) > 1 and durations else 0
        }
        
        analysis["quality"] = "good" if len(episodes) > 2 else "limited" if len(episodes) > 0 else "none"
        
        return analysis
    
    def _recognize_patterns_in_episodes(self, episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Recognize condition-like patterns in episodes."""
        # Simplified pattern recognition - would be much more sophisticated in practice
        conditions = []
        
        for i, episode in enumerate(episodes):
            # Create a condition representation from the episode
            condition = {
                "episode_id": i,
                "source_episode": episode,
                "recognized_pattern": f"pattern_{i+1}",
                "confidence": min(0.9, 0.3 + len(episode.get("events", [])) * 0.1),  # More events = higher confidence
                "characteristics": {
                    "duration_hours": 0,
                    "event_count": episode.get("event_count", 0),
                    "total_observations": sum(e.get("observation_count", 0) for e in episode.get("events", []))
                }
            }
            
            # Calculate duration
            start_time = episode.get("start_time")
            end_time = episode.get("end_time")
            if start_time and end_time:
                try:
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    if isinstance(end_time, str):
                        end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    condition["characteristics"]["duration_hours"] = (end_time - start_time).total_seconds() / 3600
                except:
                    pass
            
            conditions.append(condition)
        
        return conditions
    
    def _analyze_conditions(self, conditions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze conditions at the diagnostic scale."""
        if not conditions:
            return {"count": 0, "quality": "no_data"}
        
        confidences = [c.get("confidence", 0) for c in conditions]
        durations = [c.get("characteristics", {}).get("duration_hours", 0) for c in conditions]
        event_counts = [c.get("characteristics", {}).get("event_count", 0) for c in conditions]
        observation_counts = [c.get("characteristics", {}).get("total_observations", 0) for c in conditions]
        
        analysis = {
            "count": len(conditions),
            "avg_confidence": sum(confidences) / len(confidences) if confidences else 0,
            "confidence_range": [min(confidences), max(confidences)] if confidences else [0, 0],
            "avg_duration_hours": sum(durations) / len(durations) if durations else 0,
            "avg_event_count": sum(event_counts) / len(event_counts) if event_counts else 0,
            "avg_observation_count": sum(observation_counts) / len(observation_counts) if observation_counts else 0,
            "high_confidence_count": sum(1 for c in confidences if c > 0.7)
        }
        
        analysis["quality"] = "good" if len(conditions) > 2 else "limited" if len(conditions) > 0 else "none"
        
        return analysis
    
    def _summarize_conditions(self, conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Summarize conditions into population-level insights."""
        if not conditions:
            return []
        
        # Group conditions by similarity (simplified)
        # In practice, this would use more sophisticated similarity measures
        populations = []
        
        # For simplicity, treat each unique condition pattern as a population characteristic
        # In reality, we'd cluster similar conditions
        for condition in conditions:
            population_summary = {
                "condition_reference": condition,
                "occurrence_count": 1,  # Would be higher if we grouped similar conditions
                "average_confidence": condition.get("confidence", 0),
                "average_duration_hours": condition.get("characteristics", {}).get("duration_hours", 0),
                "average_event_count": condition.get("characteristics", {}).get("event_count", 0),
                "average_observation_count": condition.get("characteristics", {}).get("total_observations", 0),
                "representative_episode": condition.get("source_episode")
            }
            populations.append(population_summary)
        
        return populations
    
    def _analyze_populations(self, populations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze populations at the epidemiological scale."""
        if not populations:
            return {"count": 0, "quality": "no_data"}
        
        confidences = [p.get("average_confidence", 0) for p in populations]
        durations = [p.get("average_duration_hours", 0) for p in populations]
        event_counts = [p.get("average_event_count", 0) for p in populations]
        observation_counts = [p.get("average_observation_count", 0) for p in populations]
        
        analysis = {
            "count": len(populations),
            "total_occurrences": sum(p.get("occurrence_count", 0) for p in populations),
            "avg_confidence": sum(confidences) / len(confidences) if confidences else 0,
            "avg_duration_hours": sum(durations) / len(durations) if durations else 0,
            "avg_event_count": sum(event_counts) / len(event_counts) if event_counts else 0,
            "avg_observation_count": sum(observation_counts) / len(observation_counts) if observation_counts else 0,
            "confidence_range": [min(confidences), max(confidences)] if confidences else [0, 0],
            "high_confidence_populations": sum(1 for c in confidences if c > 0.7)
        }
        
        analysis["quality"] = "good" if len(populations) > 2 else "limited" if len(populations) > 0 else "none"
        
        return analysis
    
    def _derive_principles_from_populations(self, populations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Derive general care principles from population-level analysis."""
        principles = []
        
        if not populations:
            return principles
        
        # Analyze what the population data suggests about care principles
        avg_confidence = sum(p.get("average_confidence", 0) for p in populations) / len(populations) if populations else 0
        high_confidence_count = sum(1 for p in populations if p.get("average_confidence", 0) > 0.7)
        
        # Derive principles based on patterns in the data
        if high_confidence_count > len(populations) * 0.6:  # Majority high confidence
            principles.append({
                "principle": "consistent_monitoring_valuable",
                "description": "Regular monitoring and observation yields valuable insights for care decisions",
                "supporting_evidence": "High confidence in population patterns suggests consistent observation is beneficial",
                "applicability": "broad",
                "strength": "strong" if avg_confidence > 0.8 else "moderate"
            })
        
        # Look for duration patterns
        avg_duration = sum(p.get("average_duration_hours", 0) for p in populations) / len(populations) if populations else 0
        if avg_duration > 24:  # Average duration more than a day
            principles.append({
                "principle": "longitudinal_perspective_important",
                "description": "Understanding care requires longitudinal perspective beyond single encounters",
                "supporting_evidence": "Average condition duration exceeds 24 hours",
                "applicability": "chronic_care",
                "strength": "moderate"
            })
        
        # Look for observation intensity patterns
        avg_observations = sum(p.get("average_observation_count", 0) for p in populations) / len(populations) if populations else 0
        if avg_observations > 10:  # High observation density
            principles.append({
                "principle": "detailed_observation_informs_care",
                "description": "Rich observation data enables more precise care understanding",
                "supporting_evidence": "High average observation count per condition",
                "applicability": "complex_cases",
                "strength": "strong" if avg_observations > 20 else "moderate"
            })
        
        # Add general principles that always apply
        principles.append({
            "principle": "holistic_assessment_essential",
            "description": "Effective care requires understanding across multiple scales and dimensions",
            "supporting_evidence": "Hierarchical analysis reveals insights at multiple scales",
            "applicability": "universal",
            "strength": "strong"
        })
        
        principles.append({
            "principle": "context_mediates_interpretation",
            "description": "The meaning of observations depends on their temporal and contextual relationships",
            "supporting_evidence": "Events and episodes provide different insights than isolated observations",
            "applicability": "universal",
            "strength": "strong"
        })
        
        return principles
    
    def _analyze_principles(self, principles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze derived principles at the highest scale."""
        if not principles:
            return {"count": 0, "quality": "no_data"}
        
        strengths = [p.get("strength", "weak") for p in principles]
        strength_values = {"weak": 1, "moderate": 2, "strong": 3}
        numeric_strengths = [strength_values.get(s, 1) for s in strengths]
        
        applicability = [p.get("applicability", "unknown") for p in principles]
        
        analysis = {
            "count": len(principles),
            "avg_strength": sum(numeric_strengths) / len(numeric_strengths) if numeric_strengths else 0,
            "strength_distribution": {
                "weak": strengths.count("weak"),
                "moderate": strengths.count("moderate"),
                "strong": strengths.count("strong")
            },
            "applicability_distribution": {
                "universal": applicability.count("universal"),
                "broad": applicability.count("broad"),
                "chronic_care": applicability.count("chronic_care"),
                "complex_cases": applicability.count("complex_cases"),
                "unknown": applicability.count("unknown")
            },
            "strong_principles": [p for p in principles if p.get("strength") == "strong"]
        }
        
        analysis["quality"] = "good" if len(principles) > 3 else "limited" if len(principles) > 0 else "none"
        
        return analysis
    
    def _generate_cross_scale_insights(self, 
                                     obs_analysis: Dict[str, Any],
                                     evt_analysis: Dict[str, Any],
                                     ep_analysis: Dict[str, Any],
                                     cond_analysis: Dict[str, Any],
                                     pop_analysis: Dict[str, Any],
                                     prin_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate insights that span multiple scales of understanding."""
        insights = []
        
        # Observation to Event insight
        if obs_analysis.get("count", 0) > 0 and evt_analysis.get("count", 0) > 0:
            obs_per_event = evt_analysis.get("avg_observations_per_event", 0)
            if obs_per_event > 5:
                insights.append({
                    "scale_transition": "observation_to_event",
                    "insight": "Observations cluster meaningfully into distinct events",
                    "evidence": f"Average of {obs_per_event:.1f} observations per event",
                    "implication": "Event-based organization captures meaningful patterns in observation data"
                })
        
        # Event to Episode insight
        if evt_analysis.get("count", 0) > 0 and ep_analysis.get("count", 0) > 0:
            events_per_episode = ep_analysis.get("avg_events_per_episode", 0)
            if events_per_episode > 1.5:
                insights.append({
                    "scale_transition": "event_to_episode",
                    "insight": "Events group into meaningful episodes over time",
                    "evidence": f"Average of {events_per_episode:.1f} events per episode",
                    "implication": "Episodic organization reveals temporal patterns in care"
                })
        
        # Episode to Condition insight
        if ep_analysis.get("count", 0) > 0 and cond_analysis.get("count", 0) > 0:
            avg_confidence = cond_analysis.get("avg_confidence", 0)
            if avg_confidence > 0.5:
                insights.append({
                    "scale_transition": "episode_to_condition",
                    "insight": "Episodes reveal recognizable condition patterns",
                    "evidence": f"Average pattern recognition confidence: {avg_confidence:.2f}",
                    "implication": "Condition-level thinking provides diagnostic value beyond episodic description"
                })
        
        # Condition to Population insight
        if cond_analysis.get("count", 0) > 0 and pop_analysis.get("count", 0) > 0:
            high_conf_conditions = cond_analysis.get("high_confidence_count", 0)
            total_conditions = cond_analysis.get("count", 0)
            if total_conditions > 0 and high_conf_conditions / total_conditions > 0.5:
                insights.append({
                    "scale_transition": "condition_to_population",
                    "insight": "Condition patterns aggregate to population-level insights",
                    "evidence": f"{high_conf_conditions}/{total_conditions} conditions show high confidence",
                    "implication": "Population health perspectives emerge from individual condition patterns"
                })
        
        # Population to Principle insight
        if pop_analysis.get("count", 0) > 0 and prin_analysis.get("count", 0) > 0:
            strong_principles = prin_analysis.get("strength_distribution", {}).get("strong", 0)
            if strong_principles > 0:
                insights.append({
                    "scale_transition": "population_to_principle",
                    "insight": "Population patterns suggest generalizable care principles",
                    "evidence": f"{strong_principles} strong principles derived from population analysis",
                    "implication": "Specific observations can inform general care guidelines through abstraction"
                })
        
        return insights
    
    def _assess_hierarchical_coherence(self,
                                     obs_analysis: Dict[str, Any],
                                     evt_analysis: Dict[str, Any],
                                     ep_analysis: Dict[str, Any],
                                     cond_analysis: Dict[str, Any],
                                     pop_analysis: Dict[str, Any],
                                     prin_analysis: Dict[str, Any]) -> float:
        """Assess how well the scales cohere with each other."""
        # Simple coherence measure based on data flow and quality ratings
        quality_scores = {
            "none": 0.0,
            "limited": 0.5,
            "good": 1.0
        }
        
        qualities = [
            obs_analysis.get("quality", "none"),
            evt_analysis.get("quality", "none"),
            ep_analysis.get("quality", "none"),
            cond_analysis.get("quality", "none"),
            pop_analysis.get("quality", "none"),
            prin_analysis.get("quality", "none")
        ]
        
        numeric_qualities = [quality_scores.get(q, 0.0) for q in qualities]
        
        # Coherence is higher when adjacent scales have similar quality
        # and when there's a reasonable progression of information
        if len(numeric_qualities) < 2:
            return 0.0
        
        # Adjacent scale quality differences (lower difference = better coherence)
        adjacent_diffs = []
        for i in range(len(numeric_qualities) - 1):
            diff = abs(numeric_qualities[i] - numeric_qualities[i+1])
            adjacent_diffs.append(diff)
        
        avg_adjacent_diff = sum(adjacent_diffs) / len(adjacent_diffs) if adjacent_diffs else 0
        
        # Overall quality progression (should generally increase or stay stable)
        quality_trend = 0
        if len(numeric_qualities) >= 3:
            # Check if quality generally improves or stays stable up the hierarchy
            early_avg = sum(numeric_qualities[:2]) / 2
            late_avg = sum(numeric_qualities[-2:]) / 2
            if late_avg >= early_avg:
                quality_trend = 1.0  # Improving or stable
            else:
                quality_trend = 0.5  # Declining
        
        # Combine metrics
        coherence = (1.0 - min(avg_adjacent_diff, 1.0)) * 0.6 + quality_trend * 0.4
        return max(0.0, min(1.0, coherence))
    
    def _determine_abstraction_level(self,
                                   obs_analysis: Dict[str, Any],
                                   evt_analysis: Dict[str, Any],
                                   ep_analysis: Dict[str, Any],
                                   cond_analysis: Dict[str, Any],
                                   pop_analysis: Dict[str, Any],
                                   prin_analysis: Dict[str, Any]) -> str:
        """Determine the highest meaningful level of abstraction achieved."""
        # Check which scales have meaningful data
        scales_meaningful = []
        
        if obs_analysis.get("count", 0) > 0:
            scales_meaningful.append(ScaleLevel.OBSERVATION)
        if evt_analysis.get("count", 0) > 0:
            scales_meaningful.append(ScaleLevel.EVENT)
        if ep_analysis.get("count", 0) > 0:
            scales_meaningful.append(ScaleLevel.EPISODE)
        if cond_analysis.get("count", 0) > 0:
            scales_meaningful.append(ScaleLevel.CONDITION)
        if pop_analysis.get("count", 0) > 0:
            scales_meaningful.append(ScaleLevel.POPULATION)
        if prin_analysis.get("count", 0) > 0:
            scales_meaningful.append(ScaleLevel.PRINCIPLE)
        
        if not scales_meaningful:
            return "none"
        
        # Return the highest scale achieved
        highest_scale = scales_meaningful[-1]
        return highest_scale.value
    
    def _get_default_result(self) -> Dict[str, Any]:
        """Return default result for empty or invalid input."""
        return {
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "time_window_hours": 0,
            "input_observation_count": 0,
            "scales": {
                "observation": {"count": 0, "quality": "none"},
                "event": {"count": 0, "quality": "none"},
                "episode": {"count": 0, "quality": "none"},
                "condition": {"count": 0, "quality": "none"},
                "population": {"count": 0, "quality": "none"},
                "principle": {"count": 0, "quality": "none"}
            },
            "cross_scale_insights": [],
            "hierarchical_coherence_score": 0.0,
            "abstraction_level": "none",
            "error": "No observations provided for analysis"
        }
    
    def _get_default_error_result(self, error_msg: str) -> Dict[str, Any]:
        """Return error result for failed analysis."""
        return {
            "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
            "time_window_hours": 0,
            "input_observation_count": 0,
            "scales": {
                "observation": {"count": 0, "quality": "error"},
                "event": {"count": 0, "quality": "error"},
                "episode": {"count": 0, "quality": "error"},
                "condition": {"count": 0, "quality": "error"},
                "population": {"count": 0, "quality": "error"},
                "principle": {"count": 0, "quality": "error"}
            },
            "cross_scale_insights": [],
            "hierarchical_coherence_score": 0.0,
            "abstraction_level": "error",
            "error": error_msg
        }

# Global instance for reuse
_hierarchical_service = None

def get_hierarchical_service() -> HierarchicalUnderstandingService:
    """Get or create the global Hierarchical Understanding service instance."""
    global _hierarchical_service
    if _hierarchical_service is None:
        _hierarchical_service = HierarchicalUnderstandingService()
    return _hierarchical_service

def analyze_hierarchical_care_situation(observations: List[Dict[str, Any]] = None,
                                       time_window_hours: int = 24) -> Dict[str, Any]:
    """
    Convenience function to analyze a care situation across multiple scales.
    
    Args:
        observations: List of specific observations (timestamped data points)
        time_window_hours: Time window for analysis in hours
        
    Returns:
        Multi-scale hierarchical analysis from observations to principles
    """
    service = get_hierarchical_service()
    return service.analyze_hierarchical_structure(observations or [], time_window_hours)