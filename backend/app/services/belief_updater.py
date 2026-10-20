"""Hierarchical Bayesian belief-updating service with enhanced likelihood functions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import re
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.belief_state import BeliefState
from app.models.evidence import Evidence
from app.models.identity import Person
from app.core.evidence_enums import EvidenceType


class BeliefUpdaterService:
    """Service for updating beliefs over latent states using Evidence."""

    def __init__(self, db: Session):
        self.db = db

    def _get_prior(self, person_id: str) -> Dict[str, Dict[str, float]]:
        """Retrieve the most recent belief state for a person."""
        latest = (
            self.db.query(BeliefState)
            .filter(BeliefState.person_id == person_id)
            .order_by(BeliefState.updated_at.desc())
            .first()
        )
        if latest and latest.state_variables:
            return latest.state_variables
        # Return a vague prior if none exists
        return {}

    def _likelihood_from_evidence(self, evidence: Evidence) -> Dict[str, Dict[str, float]]:
        """Convert an Evidence item into a likelihood distribution based on evidence type.
        
        Enhanced version that maps different evidence types to appropriate likelihood functions.
        """
        likelihoods = {}
        
        # Map evidence type to appropriate likelihood function
        try:
            evidence_type = EvidenceType(evidence.type) if evidence.type else None
        except ValueError:
            evidence_type = None
        
        # Extract numeric values from evidence text
        numeric_values = self._extract_numeric_values(evidence.source_text or "")
        
        if evidence_type == EvidenceType.OBSERVATION:
            # Observations typically inform symptom states, vital signs, etc.
            if numeric_values:
                # Assume first numeric value is symptom severity (0-10 scale)
                severity = max(0, min(10, numeric_values[0]))  # Clamp to 0-10
                # Convert to 0-1 scale for our model
                normalized_severity = severity / 10.0
                likelihoods["symptom_severity"] = {
                    "mean": normalized_severity,
                    "variance": 0.01  # High confidence in observations
                }
            # Also extract heart rate, temperature, etc. if present
            if len(numeric_values) > 1:
                # Second value might be heart rate
                hr = numeric_values[1]
                if 40 <= hr <= 200:  # Reasonable HR range
                    likelihoods["heart_rate"] = {
                        "mean": hr,
                        "variance": 4.0  # Moderate confidence
                    }
            if len(numeric_values) > 2:
                # Third value might be temperature
                temp = numeric_values[2]
                if 95 <= temp <= 108:  # Reasonable temperature range (F)
                    likelihoods["body_temperature"] = {
                        "mean": temp,
                        "variance": 0.1
                    }
                    
        elif evidence_type == EvidenceType.MEDICATION_TAKEN:
            # Medication evidence informs medication efficacy and adherence
            if numeric_values:
                # Dosage information
                dosage = numeric_values[0]
                likelihoods["medication_adherence"] = {
                    "mean": min(1.0, dosage / 100.0),  # Assume 100mg is full dose
                    "variance": 0.04
                }
                # If we have time information, inform about timing effects
                if len(numeric_values) > 1:
                    hours_since = numeric_values[1]
                    likelihoods["medication_timing"] = {
                        "mean": hours_since,
                        "variance": 1.0
                    }
                    
        elif evidence_type == EvidenceType.SYMPTOM_REPORT:
            # Direct symptom reports
            if numeric_values:
                severity = max(0, min(10, numeric_values[0]))
                normalized_severity = severity / 10.0
                likelihoods["symptom_severity"] = {
                    "mean": normalized_severity,
                    "variance": 0.02  # Slightly less confidence than direct observation
                }
                
        elif evidence_type == EvidenceType.VITAL_SIGN:
            # Vital signs are high-confidence measurements
            if len(numeric_values) >= 3:
                # Assume HR, BP, Temp
                hr, sys_bp, temp = numeric_values[0], numeric_values[1], numeric_values[2]
                if 40 <= hr <= 200:
                    likelihoods["heart_rate"] = {"mean": hr, "variance": 2.0}
                if 70 <= sys_bp <= 200:
                    likelihoods["blood pressure_systolic"] = {"mean": sys_bp, "variance": 9.0}
                if 95 <= temp <= 108:
                    likelihoods["body_temperature"] = {"mean": temp, "variance": 0.05}
                    
        elif evidence_type == EvidenceType.BEHAVIOURAL_OBSERVATION:
            # Behavioral observations inform mood, agitation, etc.
            if numeric_values:
                # Assume first value is agitation level (0-10)
                agitation = max(0, min(10, numeric_values[0]))
                normalized_agitation = agitation / 10.0
                likelihoods["agitation_level"] = {
                    "mean": normalized_agitation,
                    "variance": 0.03
                }
            # Extract behavioral keywords from text
            text_lower = (evidence.source_text or "").lower()
            if any(word in text_lower for word in ["anxious", "worried", "nervous"]):
                likelihoods["anxiety_level"] = {"mean": 0.7, "variance": 0.04}
            if any(word in text_lower for word in ["depressed", "sad", "hopeless"]):
                likelihoods["depression_level"] = {"mean": 0.6, "variance": 0.04}
                
        # Handle textual evidence for categorical variables
        if evidence.source_text:
            text_lower = evidence.source_text.lower()
            # Mood indicators
            if any(word in text_lower for word in ["happy", "pleased", "content", "joyful"]):
                likelihoods["mood_positive"] = {"mean": 0.8, "variance": 0.05}
            if any(word in text_lower for word in ["irritable", "frustrated", "angry"]):
                likelihoods["mood_negative"] = {"mean": 0.3, "variance": 0.05}
            # Cognitive indicators
            if any(word in text_lower for word in ["confused", "disoriented", "forgetful"]):
                likelihoods["cognitive_clarity"] = {"mean": 0.3, "variance": 0.06}
            if any(word in text_lower for word in ["alert", "oriented", "clear"]):
                likelihoods["cognitive_clarity"] = {"mean": 0.8, "variance": 0.04}

        # If no specific likelihoods were generated, create a weak general likelihood
        if not likelihoods and numeric_values:
            # Use first numeric value as a general health indicator
            gen_health = max(0, min(1, numeric_values[0] / 100.0))  # Normalize to 0-1
            likelihoods["general_health"] = {
                "mean": gen_health,
                "variance": 0.1
            }

        return likelihoods

    def _extract_numeric_values(self, text: str) -> List[float]:
        """Extract all numeric values from text."""
        if not text:
            return []
        # Find all numbers (integers and floats)
        numbers = re.findall(r"[-+]?\d*\.\d+|\d+", text)
        return [float(num) for num in numbers]

    def _update_gaussian(self, prior: Dict[str, float], likelihood: Dict[str, float]) -> Dict[str, float]:
        """Update a Gaussian prior with a Gaussian likelihood (conjugate)."""
        # prior: dict with mean, variance
        # likelihood: dict with mean, variance
        prior_mean = prior.get("mean", 0.0)
        prior_var = prior.get("variance", 1.0)
        like_mean = likelihood.get("mean", 0.0)
        like_var = likelihood.get("variance", 1.0)

        # Avoid division by zero
        if prior_var <= 0:
            prior_var = 1e-6
        if like_var <= 0:
            like_var = 1e-6

        # Posterior variance
        post_var = 1.0 / (1.0 / prior_var + 1.0 / like_var)
        # Posterior mean
        post_mean = post_var * (prior_mean / prior_var + like_mean / like_var)
        return {"mean": post_mean, "variance": post_var}

    def _update_beta(self, prior_alpha: float, prior_beta: float, 
                     likelihood_alpha: float, likelihood_beta: float) -> Dict[str, float]:
        """Update a Beta prior with Beta likelihood (conjugate for binomial/proportions)."""
        post_alpha = prior_alpha + likelihood_alpha
        post_beta = prior_beta + likelihood_beta
        # Mean of Beta distribution is alpha/(alpha+beta)
        post_mean = post_alpha / (post_alpha + post_beta) if (post_alpha + post_beta) > 0 else 0.5
        # Variance of Beta distribution is (alpha*beta)/((alpha+beta)^2*(alpha+beta+1))
        post_var = (post_alpha * post_beta) / \
                  ((post_alpha + post_beta) ** 2 * (post_alpha + post_beta + 1)) \
                  if (post_alpha + post_beta) > 0 else 0.25
        return {"mean": post_mean, "variance": post_var}

    def update_belief(self, person_id: str) -> BeliefState:
        """Update belief state for a person based on all their Evidence."""
        prior = self._get_prior(person_id)
        # If prior is empty, initialize with vague priors for common variables
        if not prior:
            prior = {
                "symptom_severity": {"mean": 0.5, "variance": 1.0},
                "general_health": {"mean": 0.5, "variance": 1.0},
                "medication_adherence": {"mean": 0.5, "variance": 1.0}
            }

        # Gather evidence for the person
        evidences = (
            self.db.query(Evidence)
            .filter(Evidence.subject_id == person_id)
            .order_by(Evidence.event_time.asc())
            .all()
        )

        posterior = prior.copy()
        for ev in evidences:
            likelihood = self._likelihood_from_evidence(ev)
            # For each variable in likelihood, update posterior
            for var, like_params in likelihood.items():
                if var in posterior:
                    # Check if we should use Beta update (for proportions 0-1)
                    if var in ["medication_adherence", "symptom_severity", "general_health", 
                              "mood_positive", "mood_negative", "cognitive_clarity",
                              "anxiety_level", "depression_level", "agitation_level"]:
                        # Convert to Beta parameters
                        prior_mean = prior[var]["mean"]
                        prior_var = prior[var]["variance"]
                        # Avoid extreme values
                        prior_mean = max(0.001, min(0.999, prior_mean))
                        prior_var = max(0.001, min(prior_mean * (1 - prior_mean), prior_var))
                        
                        # Convert mean,var to alpha,beta for Beta distribution
                        if prior_var > 0 and prior_var < prior_mean * (1 - prior_mean):
                            prior_alpha = prior_mean * ((prior_mean * (1 - prior_mean) / prior_var) - 1)
                            prior_beta = (1 - prior_mean) * ((prior_mean * (1 - prior_mean) / prior_var) - 1)
                        else:
                            # Fallback to Gaussian if Beta parameters invalid
                            posterior[var] = self._update_gaussian(prior[var], like_params)
                            continue
                            
                        like_mean = like_params["mean"]
                        like_var = like_params["variance"]
                        # Avoid extreme values
                        like_mean = max(0.001, min(0.999, like_mean))
                        like_var = max(0.001, min(like_mean * (1 - like_mean), like_var))
                        
                        if like_var > 0 and like_var < like_mean * (1 - like_mean):
                            like_alpha = like_mean * ((like_mean * (1 - like_mean) / like_var) - 1)
                            like_beta = (1 - like_mean) * ((like_mean * (1 - like_mean) / like_var) - 1)
                        else:
                            # Fallback to Gaussian if Beta parameters invalid
                            posterior[var] = self._update_gaussian(prior[var], like_params)
                            continue
                            
                        # Update using Beta-Beta conjugation
                        beta_result = self._update_beta(prior_alpha, prior_beta, like_alpha, like_beta)
                        posterior[var] = beta_result
                    else:
                        # Use Gaussian-Gaussian update for other variables
                        posterior[var] = self._update_gaussian(prior[var], like_params)
                else:
                    # If variable not in prior, start with appropriate prior then update
                    if var in ["medication_adherence", "symptom_severity", "general_health", 
                              "mood_positive", "mood_negative", "cognitive_clarity",
                              "anxiety_level", "depression_level", "agitation_level"]:
                        # For proportions, start with vague Beta(1,1) which is Uniform(0,1)
                        vague_prior = {"mean": 0.5, "variance": 1/12}  # Variance of Uniform(0,1)
                    else:
                        # For other variables, start with vague Gaussian
                        vague_prior = {"mean": 0.0, "variance": 10.0}
                    posterior[var] = self._update_gaussian(vague_prior, like_params)

        # Create new belief state record
        # Generate a proper UUID
        import uuid
        belief_state = BeliefState(
            id=str(uuid.uuid4()),
            person_id=person_id,
            state_variables=posterior,
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(belief_state)
        self.db.commit()
        self.db.refresh(belief_state)
        return belief_state


def get_belief_updater_service(db: Session) -> BeliefUpdaterService:
    """Dependency injection factory."""
    return BeliefUpdaterService(db)
