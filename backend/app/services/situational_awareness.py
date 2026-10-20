"""
Situational Awareness Service for understanding social determinants, living conditions, and support networks.
"""
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import json

try:
    import spacy
    from spacy.matcher import Matcher
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

logger = logging.getLogger(__name__)

class SocialDeterminantCategory(Enum):
    """Categories of social determinants of health."""
    ECONOMIC_STABILITY = "economic_stability"
    EDUCATION_ACCESS_QUALITY = "education_access_quality"
    HEALTHCARE_ACCESS_QUALITY = "healthcare_access_quality"
    NEIGHBORHOOD_BUILT_ENVIRONMENT = "neighborhood_built_environment"
    SOCIAL_COMMUNITY_CONTEXT = "social_community_context"

class LivingConditionAspect(Enum):
    """Aspects of living conditions to assess."""
    HOUSING_STABILITY = "housing_stability"
    FOOD_SECURITY = "food_security"
    UTILITIES_ACCESS = "utilities_access"
    TRANSPORTATION_ACCESS = "transportation_access"
    SAFETY_SECURITY = "safety_security"
    CLEANLINESS_HYGIENE = "cleanliness_hygiene"

class SupportNetworkType(Enum):
    """Types of support networks."""
    FAMILY_SUPPORT = "family_support"
    FRIEND_SUPPORT = "friend_support"
    COMMUNITY_SUPPORT = "community_support"
    PROFESSIONAL_SUPPORT = "professional_support"
    RELIGIOUS_SPIRITUAL = "religious_spiritual"
    PET_ANIMAL_SUPPORT = "pet_animal_support"

class SituationalAwarenessService:
    """Service for understanding social determinants, living conditions, and support networks."""
    
    def __init__(self):
        """Initialize the Situational Awareness service."""
        self.nlp = None
        self.matcher = None
        self._init_nlp_components()
        logger.info("Situational Awareness service initialized")
    
    def _init_nlp_components(self):
        """Initialize NLP components."""
        # Initialize spaCy if available
        if SPACY_AVAILABLE:
            try:
                self.nlp = spacy.load("en_core_web_sm")
                self.matcher = Matcher(self.nlp.vocab)
                self._setup_situational_patterns()
                logger.info("spaCy components initialized")
            except Exception as e:
                logger.warning(f"Could not initialize spaCy: {e}")
                self.nlp = None
    
    def _setup_situational_patterns(self):
        """Setup patterns for situational awareness detection."""
        if not self.matcher:
            return
        
        # Economic stability patterns
        economic_patterns = [
            [{"LOWER": {"IN": ["income", "wage", "salary", "earnings", "pay"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["poverty", "debt", "loan", "credit", "bill"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["employed", "unemployed", "job", "work", "occupation"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["food", "stamp", "snap", "wic", "benefit", "assistance"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["rent", "mortgage", "eviction", "foreclosure", "homeless"]}}, {"OP": "*"}],
        ]
        self.matcher.add("ECONOMIC_STABILITY", economic_patterns)
        
        # Education access/quality patterns
        education_patterns = [
            [{"LOWER": {"IN": ["school", "college", "university", "education", "degree"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["literacy", "reading", "writing", "graduation", "diploma"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["training", "course", "class", "lesson", "tutoring"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["special", "education", "iep", "disability", "accommodation"]}}, {"OP": "*"}],
        ]
        self.matcher.add("EDUCATION_ACCESS_QUALITY", education_patterns)
        
        # Healthcare access/quality patterns
        healthcare_patterns = [
            [{"LOWER": {"IN": ["doctor", "physician", "clinic", "hospital", "appointment"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["insurance", "medicaid", "medicare", "coverage", "benefit"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["medication", "prescription", "pharmacy", "cost", "afford"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["transportation", "ride", "ambulance", "access", "barrier"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["wait", "time", "delay", "appointment", "scheduled"]}}, {"OP": "*"}],
        ]
        self.matcher.add("HEALTHCARE_ACCESS_QUALITY", healthcare_patterns)
        
        # Neighborhood/built environment patterns
        neighborhood_patterns = [
            [{"LOWER": {"IN": ["neighborhood", "community", "area", "zone", "block", "street"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["safe", "unsafe", "crime", "violence", "pollution", "clean"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["park", "playground", "recreation", "exercise", "walkable"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["grocery", "store", "market", "food", "desert", "fresh"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["housing", "apartment", "home", "condition", "repair", "quality"]}}, {"OP": "*"}],
        ]
        self.matcher.add("NEIGHBORHOOD_BUILT_ENVIRONMENT", neighborhood_patterns)
        
        # Social/community context patterns
        social_patterns = [
            [{"LOWER": {"IN": ["family", "friend", "relative", "marriage", "divorce", "single"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["support", "help", "caregiver", "network", "church", "religion"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["discrimination", "racism", "prejudice", "bias", "stigma", "isolated"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["language", "english", "speak", "communication", "interpreter"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["social", "activity", "event", "group", "club", "organization"]}}, {"OP": "*"}],
        ]
        self.matcher.add("SOCIAL_COMMUNITY_CONTEXT", social_patterns)
        
        # Living conditions patterns
        living_patterns = [
            [{"LOWER": {"IN": ["housing", "apartment", "home", "rent", "mortgage", "eviction"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["food", "hungry", "hunger", "meal", "eat", "starving", "malnutrition"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["heat", "air", "conditioning", "electric", "power", "water", "utility"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["transport", "bus", "car", "taxi", "ride", "walk", "bike", "mobile"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["safe", "unsafe", "danger", "crime", "lock", "alarm", "secure"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["clean", "dirty", "trash", "pest", "rodent", "mold", "leak"]}}, {"OP": "*"}],
        ]
        self.matcher.add("LIVING_CONDITIONS", living_patterns)
        
        # Support network patterns
        support_patterns = [
            [{"LOWER": {"IN": ["family", "mother", "father", "parent", "sister", "brother", "spouse", "husband", "wife"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["friend", "neighbor", "neighbour", "buddy", "pal", "companion"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["church", "temple", "mosque", "synagogue", "religious", "faith", "spiritual"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["doctor", "nurse", "therapist", "counselor", "social", "worker", "case", "manager"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["pet", "dog", "cat", "animal", "companion", "therapy"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["support", "help", "assist", "care", "visit", "call", "check"]}}, {"OP": "*"}],
        ]
        self.matcher.add("SUPPORT_NETWORK", support_patterns)
    
    def analyze_situational_awareness(self, text: str) -> Dict[str, Any]:
        """
        Analyze text for social determinants, living conditions, and support networks.
        
        Args:
            text: Input text to analyze
            
        Returns:
            Dictionary containing situational awareness analysis
        """
        if not text or not text.strip():
            return self._get_default_result()
        
        # Clean and preprocess text
        cleaned_text = self._preprocess_text(text)
        
        # Initialize result
        result = {
            "text": text,
            "cleaned_text": cleaned_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "social_determinants": {},
            "living_conditions": {},
            "support_networks": {},
            "risk_factors": [],
            "protective_factors": [],
            "overall_assessment": {},
            "analysis_details": {}
        }
        
        try:
            # Analyze social determinants
            result["social_determinants"] = self._analyze_social_determinants(cleaned_text)
            
            # Analyze living conditions
            result["living_conditions"] = self._analyze_living_conditions(cleaned_text)
            
            # Analyze support networks
            result["support_networks"] = self._analyze_support_networks(cleaned_text)
            
            # Identify risk and protective factors
            result["risk_factors"], result["protective_factors"] = self._identify_factors(
                result["social_determinants"], 
                result["living_conditions"], 
                result["support_networks"]
            )
            
            # Generate overall assessment
            result["overall_assessment"] = self._generate_overall_assessment(
                result["social_determinants"],
                result["living_conditions"],
                result["support_networks"],
                result["risk_factors"],
                result["protective_factors"]
            )
            
            # Generate analysis details
            result["analysis_details"] = self._generate_analysis_details(cleaned_text)
            
        except Exception as e:
            logger.error(f"Error in situational awareness analysis: {e}")
            result["error"] = str(e)
        
        return result
    
    def _preprocess_text(self, text: str) -> str:
        """Preprocess text for analysis."""
        # Convert to lowercase
        text = text.lower().strip()
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove special characters but keep basic punctuation
        text = re.sub(r'[^\w\s\.\,\!\?\:\;\-\(\)]', '', text)
        return text
    
    def _analyze_social_determinants(self, text: str) -> Dict[str, Any]:
        """Analyze social determinants of health."""
        determinants = {}
        
        if self.matcher and self.nlp:
            try:
                doc = self.nlp(text)
                matches = self.matcher(doc)
                
                # Count matches by category
                category_counts = {}
                for match_id, start, end in matches:
                    category_label = self.nlp.vocab.strings[match_id]
                    category_counts[category_label] = category_counts.get(category_label, 0) + 1
                
                # Convert to categories and calculate scores
                for category_label, count in category_counts.items():
                    try:
                        category = SocialDeterminantCategory[category_label]
                        # Normalize score (0-1) based on keyword matches
                        # Assume max 5 keyword matches per category for normalization
                        score = min(1.0, count / 5.0)
                        determinants[category.value] = {
                            "present": count > 0,
                            "score": score,
                            "keyword_matches": count,
                            "description": self._get_social_determinant_description(category)
                        }
                    except KeyError:
                        # Skip if label doesn't match enum
                        continue
            except Exception as e:
                logger.warning(f"Error in spaCy social determinants analysis: {e}")
                # Fallback to rule-based
                return self._rule_based_social_determinants(text)
        else:
            # Rule-based fallback
            return self._rule_based_social_determinants(text)
        
        return determinants
    
    def _rule_based_social_determinants(self, text: str) -> Dict[str, Any]:
        """Rule-based fallback for social determinants analysis."""
        determinants = {}
        text_lower = text.lower()
        
        # Define keywords for each category
        category_keywords = {
            SocialDeterminantCategory.ECONOMIC_STABILITY: [
                "income", "wage", "salary", "earnings", "pay", "poverty", "debt", 
                "loan", "credit", "bill", "employed", "unemployed", "job", "work", 
                "occupation", "food stamp", "snap", "wic", "benefit", "assistance",
                "rent", "mortgage", "eviction", "foreclosure", "homeless"
            ],
            SocialDeterminantCategory.EDUCATION_ACCESS_QUALITY: [
                "school", "college", "university", "education", "degree", "literacy", 
                "reading", "writing", "graduation", "diploma", "training", "course", 
                "class", "lesson", "tutoring", "special education", "iep", "disability", 
                "accommodation"
            ],
            SocialDeterminantCategory.HEALTHCARE_ACCESS_QUALITY: [
                "doctor", "physician", "clinic", "hospital", "appointment", "insurance", 
                "medicaid", "medicare", "coverage", "benefit", "medication", 
                "prescription", "pharmacy", "cost", "afford", "transportation", 
                "ride", "ambulance", "access", "barrier", "wait", "time", "delay", 
                "appointment", "scheduled"
            ],
            SocialDeterminantCategory.NEIGHBORHOOD_BUILT_ENVIRONMENT: [
                "neighborhood", "community", "area", "zone", "block", "street", "safe", 
                "unsafe", "crime", "violence", "pollution", "clean", "park", "playground", 
                "recreation", "exercise", "walkable", "grocery", "store", "market", 
                "food", "desert", "fresh", "housing", "apartment", "home", "condition", 
                "repair", "quality"
            ],
            SocialDeterminantCategory.SOCIAL_COMMUNITY_CONTEXT: [
                "family", "friend", "relative", "marriage", "divorce", "single", "support", 
                "help", "caregiver", "network", "church", "religion", "discrimination", 
                "racism", "prejudice", "bias", "stigma", "isolated", "language", "english", 
                "speak", "communication", "interpreter", "social", "activity", "event", 
                "group", "club", "organization"
            ]
        }
        
        # Analyze each category
        for category, keywords in category_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in text_lower)
            # Normalize score (0-1) based on keyword matches
            # Assume max 10 keyword matches per category for normalization
            score = min(1.0, matches / 10.0)
            
            determinants[category.value] = {
                "present": matches > 0,
                "score": score,
                "keyword_matches": matches,
                "description": self._get_social_determinant_description(category)
            }
        
        return determinants
    
    def _analyze_living_conditions(self, text: str) -> Dict[str, Any]:
        """Analyze living conditions."""
        conditions = {}
        
        if self.matcher and self.nlp:
            try:
                doc = self.nlp(text)
                matches = self.matcher(doc)
                
                # Count matches by aspect (we'll use LIVING_CONDITIONS as a catch-all)
                living_condition_matches = 0
                for match_id, start, end in matches:
                    aspect_label = self.nlp.vocab.strings[match_id]
                    if aspect_label == "LIVING_CONDITIONS":
                        living_condition_matches += 1
                
                # For now, provide a general living conditions assessment
                # In a more sophisticated version, we'd break this down by aspect
                score = min(1.0, living_condition_matches / 5.0)
                conditions["overall"] = {
                    "present": living_condition_matches > 0,
                    "score": score,
                    "keyword_matches": living_condition_matches,
                    "description": "General living conditions assessment"
                }
                
                # Add specific aspect analysis using keyword matching
                conditions.update(self._rule_based_living_conditions(text))
                
            except Exception as e:
                logger.warning(f"Error in spaCy living conditions analysis: {e}")
                conditions = self._rule_based_living_conditions(text)
        else:
            conditions = self._rule_based_living_conditions(text)
        
        return conditions
    
    def _rule_based_living_conditions(self, text: str) -> Dict[str, Any]:
        """Rule-based fallback for living conditions analysis."""
        conditions = {}
        text_lower = text.lower()
        
        # Define keywords for each living condition aspect
        aspect_keywords = {
            LivingConditionAspect.HOUSING_STABILITY: [
                "housing", "apartment", "home", "rent", "mortgage", "eviction", 
                "foreclosure", "homeless", "stable", "unstable", "temporary", "permanent"
            ],
            LivingConditionAspect.FOOD_SECURITY: [
                "food", "hungry", "hunger", "meal", "eat", "starving", "malnutrition", 
                "groceries", "supplement", "stamp", "snap", "wic", "food bank", "pantry"
            ],
            LivingConditionAspect.UTILITIES_ACCESS: [
                "heat", "air", "conditioning", "electric", "power", "water", "utility", 
                "gas", "sewage", "trash", "internet", "phone", "service", "bill"
            ],
            LivingConditionAspect.TRANSPORTATION_ACCESS: [
                "transport", "bus", "car", "taxi", "ride", "walk", "bike", "mobile", 
                "public", "transportation", "driver", "license", "gas", "fuel", "parking"
            ],
            LivingConditionAspect.SAFETY_SECURITY: [
                "safe", "unsafe", "danger", "crime", "violence", "theft", "robbery", 
                "assault", "harassment", "lock", "alarm", "secure", "protect", "police"
            ],
            LivingConditionAspect.CLEANLINESS_HYGIENE: [
                "clean", "dirty", "trash", "pest", "rodent", "mold", "leak", "water", 
                "damage", "repair", "hygiene", "sanitation", "bathroom", "toilet", "shower"
            ]
        }
        
        # Analyze each aspect
        for aspect, keywords in aspect_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in text_lower)
            # Normalize score (0-1) based on keyword matches
            # Assume max 8 keyword matches per aspect for normalization
            score = min(1.0, matches / 8.0)
            
            conditions[aspect.value] = {
                "present": matches > 0,
                "score": score,
                "keyword_matches": matches,
                "description": self._get_living_condition_description(aspect)
            }
        
        return conditions
    
    def _analyze_support_networks(self, text: str) -> Dict[str, Any]:
        """Analyze support networks."""
        networks = {}
        
        if self.matcher and self.nlp:
            try:
                doc = self.nlp(text)
                matches = self.matcher(doc)
                
                # Count matches by support type
                support_counts = {}
                for match_id, start, end in matches:
                    network_label = self.nlp.vocab.strings[match_id]
                    if network_label == "SUPPORT_NETWORK":
                        # We need to look at the actual matched text to determine type
                        matched_text = doc[start:end].text.lower()
                        # Simple classification based on keywords in the match
                        if any(word in matched_text for word in ["family", "mother", "father", "parent", "sister", "brother", "spouse", "husband", "wife"]):
                            network_type = "FAMILY_SUPPORT"
                        elif any(word in matched_text for word in ["friend", "neighbor", "buddy", "pal", "companion"]):
                            network_type = "FRIEND_SUPPORT"
                        elif any(word in matched_text for word in ["church", "temple", "mosque", "synagogue", "religious", "faith", "spiritual"]):
                            network_type = "RELIGIOUS_SPIRITUAL"
                        elif any(word in matched_text for word in ["doctor", "nurse", "therapist", "counselor", "social", "worker", "case", "manager"]):
                            network_type = "PROFESSIONAL_SUPPORT"
                        elif any(word in matched_text for word in ["pet", "dog", "cat", "animal", "companion", "therapy"]):
                            network_type = "PET_ANIMAL_SUPPORT"
                        else:
                            network_type = "COMMUNITY_SUPPORT"  # Default
                        
                        support_counts[network_type] = support_counts.get(network_type, 0) + 1
                
                # Convert to network types and calculate scores
                for network_label, count in support_counts.items():
                    try:
                        network_type = SupportNetworkType[network_label]
                        # Normalize score (0-1) based on keyword matches
                        # Assume max 5 keyword matches per network type for normalization
                        score = min(1.0, count / 5.0)
                        networks[network_type.value] = {
                            "present": count > 0,
                            "score": score,
                            "keyword_matches": count,
                            "description": self._get_support_network_description(network_type)
                        }
                    except KeyError:
                        # Skip if label doesn't match enum
                        continue
            except Exception as e:
                logger.warning(f"Error in spaCy support networks analysis: {e}")
                # Fallback to rule-based
                return self._rule_based_support_networks(text)
        else:
            # Rule-based fallback
            return self._rule_based_support_networks(text)
        
        return networks
    
    def _rule_based_support_networks(self, text: str) -> Dict[str, Any]:
        """Rule-based fallback for support networks analysis."""
        networks = {}
        text_lower = text.lower()
        
        # Define keywords for each support network type
        network_keywords = {
            SupportNetworkType.FAMILY_SUPPORT: [
                "family", "mother", "father", "parent", "sister", "brother", 
                "spouse", "husband", "wife", "child", "children", "relative", "kin"
            ],
            SupportNetworkType.FRIEND_SUPPORT: [
                "friend", "neighbor", "neighbour", "buddy", "pal", "companion", 
                "mate", "fellow", "associate", "ally", "confidant"
            ],
            SupportNetworkType.COMMUNITY_SUPPORT: [
                "community", "group", "organization", "association", "club", "society", 
                "neighborhood", "volunteer", "service", "program", "resource", "center"
            ],
            SupportNetworkType.PROFESSIONAL_SUPPORT: [
                "doctor", "nurse", "therapist", "counselor", "social", "worker", 
                "case", "manager", "provider", "specialist", "clinician", "professional"
            ],
            SupportNetworkType.RELIGIOUS_SPIRITUAL: [
                "church", "temple", "mosque", "synagogue", "religious", "faith", 
                "spiritual", "prayer", "worship", "clergy", "pastor", "priest", "rabbi", "imam"
            ],
            SupportNetworkType.PET_ANIMAL_SUPPORT: [
                "pet", "dog", "cat", "animal", "companion", "therapy", "service", 
                "emotional", "support", "animal", "assisted"
            ]
        }
        
        # Analyze each network type
        for network_type, keywords in network_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in text_lower)
            # Normalize score (0-1) based on keyword matches
            # Assume max 8 keyword matches per network type for normalization
            score = min(1.0, matches / 8.0)
            
            networks[network_type.value] = {
                "present": matches > 0,
                "score": score,
                "keyword_matches": matches,
                "description": self._get_support_network_description(network_type)
            }
        
        return networks
    
    def _identify_factors(self, 
                         social_determinants: Dict[str, Any], 
                         living_conditions: Dict[str, Any], 
                         support_networks: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Identify risk and protective factors based on the analysis."""
        risk_factors = []
        protective_factors = []
        
        # Analyze social determinants for risk/protective factors
        for category, data in social_determinants.items():
            if isinstance(data, dict) and "score" in data:
                score = data["score"]
                # Low scores in positive determinants are risk factors
                # High scores in positive determinants are protective factors
                if score < 0.3:  # Low presence/risk
                    risk_factors.append({
                        "category": "social_determinant",
                        "factor": category.replace("_", " ").title(),
                        "description": data.get("description", ""),
                        "score": score,
                        "impact": "medium" if score < 0.1 else "low"
                    })
                elif score > 0.7:  # High presence/protective
                    protective_factors.append({
                        "category": "social_determinant",
                        "factor": category.replace("_", " ").Title(),
                        "description": data.get("description", ""),
                        "score": score,
                        "impact": "medium" if score > 0.9 else "low"
                    })
        
        # Analyze living conditions for risk/protective factors
        for aspect, data in living_conditions.items():
            if isinstance(data, dict) and "score" in data:
                score = data["score"]
                # For living conditions, we need to interpret what high/low scores mean
                # Housing stability: high score = good (protective), low score = bad (risk)
                # Food security: high score = good (protective), low score = bad (risk)
                # Utilities access: high score = good (protective), low score = bad (risk)
                # Transportation access: high score = good (protective), low score = bad (risk)
                # Safety/security: high score = good (protective), low score = bad (risk)
                # Cleanliness/hygiene: high score = good (protective), low score = bad (risk)
                
                if score < 0.3:  # Poor conditions = risk
                    risk_factors.append({
                        "category": "living_condition",
                        "factor": aspect.replace("_", " ").title(),
                        "description": data.get("description", ""),
                        "score": score,
                        "impact": "high" if score < 0.1 else "medium"
                    })
                elif score > 0.7:  # Good conditions = protective
                    protective_factors.append({
                        "category": "living_condition",
                        "factor": aspect.replace("_", " ").title(),
                        "description": data.get("description", ""),
                        "score": score,
                        "impact": "high" if score > 0.9 else "medium"
                    })
        
        # Analyze support networks for risk/protective factors
        for network_type, data in support_networks.items():
            if isinstance(data, dict) and "score" in data:
                score = data["score"]
                # For support networks, high score = good (protective), low score = risk (lack of support)
                if score < 0.3:  # Lack of support = risk
                    risk_factors.append({
                        "category": "support_network",
                        "factor": network_type.replace("_", " ").title(),
                        "description": data.get("description", ""),
                        "score": score,
                        "impact": "medium" if score < 0.1 else "low"
                    })
                elif score > 0.7:  # Strong support = protective
                    protective_factors.append({
                        "category": "support_network",
                        "factor": network_type.replace("_", " ").title(),
                        "description": data.get("description", ""),
                        "score": score,
                        "impact": "medium" if score > 0.9 else "low"
                    })
        
        return risk_factors, protective_factors
    
    def _generate_overall_assessment(self,
                                   social_determinants: Dict[str, Any],
                                   living_conditions: Dict[str, Any],
                                   support_networks: Dict[str, Any],
                                   risk_factors: List[Dict[str, Any]],
                                   protective_factors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate overall situational awareness assessment."""
        # Calculate average scores for each domain
        def average_score(domain_data):
            if not domain_data:
                return 0.0
            scores = [data["score"] for data in domain_data.values() if isinstance(data, dict) and "score" in data]
            return sum(scores) / len(scores) if scores else 0.0
        
        avg_social = average_score(social_determinants)
        avg_living = average_score(living_conditions)
        avg_support = average_score(support_networks)
        
        # Overall situational awareness score (weighted average)
        overall_score = (avg_social * 0.4 + avg_living * 0.3 + avg_support * 0.3)
        
        # Risk assessment based on risk factors
        high_risk_count = sum(1 for rf in risk_factors if rf.get("impact") == "high")
        med_risk_count = sum(1 for rf in risk_factors if rf.get("impact") == "medium")
        low_risk_count = sum(1 for rf in risk_factors if rf.get("impact") == "low")
        
        if high_risk_count > 0:
            risk_level = "high"
        elif med_risk_count > 1:
            risk_level = "medium"
        elif len(risk_factors) > 0:
            risk_level = "low"
        else:
            risk_level = "minimal"
        
        # Support assessment
        support_level = "strong" if avg_support > 0.7 else "moderate" if avg_support > 0.4 else "weak"
        
        # Living conditions assessment
        living_conditions_level = "good" if avg_living > 0.7 else "moderate" if avg_living > 0.4 else "poor"
        
        # Social determinants assessment
        social_level = "favorable" if avg_social > 0.7 else "moderate" if avg_social > 0.4 else "challenging"
        
        return {
            "overall_score": overall_score,
            "risk_level": risk_level,
            "support_level": support_level,
            "living_conditions_level": living_conditions_level,
            "social_determinants_level": social_level,
            "domain_scores": {
                "social_determinants": avg_social,
                "living_conditions": avg_living,
                "support_networks": avg_support
            },
            "summary": self._generate_assessment_summary(
                overall_score, risk_level, support_level, living_conditions_level, social_level
            )
        }
    
    def _generate_assessment_summary(self, overall_score: float, risk_level: str, 
                                   support_level: str, living_conditions_level: str, 
                                   social_level: str) -> str:
        """Generate a human-readable summary of the assessment."""
        summary_parts = []
        
        # Overall assessment
        if overall_score > 0.7:
            summary_parts.append("Overall situation appears favorable")
        elif overall_score > 0.4:
            summary_parts.append("Overall situation shows moderate challenges")
        else:
            summary_parts.append("Overall situation indicates significant challenges")
        
        # Risk level
        if risk_level == "high":
            summary_parts.append("High risk factors detected requiring attention")
        elif risk_level == "medium":
            summary_parts.append("Moderate risk factors present")
        elif risk_level == "low":
            summary_parts.append("Low level risk factors identified")
        else:
            summary_parts.append("Minimal risk factors detected")
        
        # Support network
        summary_parts.append(f"Support network assessed as {support_level}")
        
        # Living conditions
        summary_parts.append(f"Living conditions evaluated as {living_conditions_level}")
        
        # Social determinants
        summary_parts.append(f"Social determinants context: {social_level}")
        
        return ". ".join(summary_parts) + "."
    
    def _generate_analysis_details(self, text: str) -> Dict[str, Any]:
        """Generate detailed analysis information."""
        return {
            "text_length": len(text),
            "word_count": len(text.split()),
            "sentence_count": len([s for s in text.split('.') if s.strip()]),
            "character_count": len(text),
            "language_detected": "english",  # Simplified - could use langdetect
            "analysis_completeness": "complete"  # Could be improved with confidence metrics
        }
    
    def _get_social_determinant_description(self, category: SocialDeterminantCategory) -> str:
        """Get description for social determinant category."""
        descriptions = {
            SocialDeterminantCategory.ECONOMIC_STABILITY: "Financial security, employment, income, expenses, debt, and material possessions",
            SocialDeterminantCategory.EDUCATION_ACCESS_QUALITY: "Access to education, literacy, educational quality, and vocational training",
            SocialDeterminantCategory.HEALTHCARE_ACCESS_QUALITY: "Access to healthcare services, health insurance coverage, and healthcare quality",
            SocialDeterminantCategory.NEIGHBORHOOD_BUILT_ENVIRONMENT: "Housing quality, transportation access, neighborhood safety, environmental quality, and food availability",
            SocialDeterminantCategory.SOCIAL_COMMUNITY_CONTEXT: "Social integration, support systems, community engagement, discrimination, and stress"
        }
        return descriptions.get(category, "Social determinant of health")
    
    def _get_living_condition_description(self, aspect: LivingConditionAspect) -> str:
        """Get description for living condition aspect."""
        descriptions = {
            LivingConditionAspect.HOUSING_STABILITY: "Residential stability, housing quality, and homelessness risk",
            LivingConditionAspect.FOOD_SECURITY: "Access to sufficient, safe, nutritious food for healthy life",
            LivingConditionAspect.UTILITIES_ACCESS: "Access to essential utilities: electricity, water, heating, cooling, sanitation",
            LivingConditionAspect.TRANSPORTATION_ACCESS: "Ability to access transportation for work, healthcare, and daily needs",
            LivingConditionAspect.SAFETY_SECURITY: "Personal safety, security from crime, and environmental hazards",
            LivingConditionAspect.CLEANLINESS_HYGIENE: "Environmental cleanliness, personal hygiene, and sanitation conditions"
        }
        return descriptions.get(aspect, "Living condition aspect")
    
    def _get_support_network_description(self, network_type: SupportNetworkType) -> str:
        """Get description for support network type."""
        descriptions = {
            SupportNetworkType.FAMILY_SUPPORT: "Emotional, practical, and financial assistance from family members",
            SupportNetworkType.FRIEND_SUPPORT: "Companionship, emotional support, and practical help from friends",
            SupportNetworkType.COMMUNITY_SUPPORT: "Assistance from community groups, organizations, and local resources",
            SupportNetworkType.PROFESSIONAL_SUPPORT: "Support from healthcare, social service, and other professional providers",
            SupportNetworkType.RELIGIOUS_SPIRITUAL: "Spiritual, emotional, and community support from religious or faith-based groups",
            SupportNetworkType.PET_ANIMAL_SUPPORT: "Emotional support, companionship, and therapeutic benefits from pets or animals"
        }
        return descriptions.get(network_type, "Support network resource")
    
    def _get_default_result(self) -> Dict[str, Any]:
        """Return default result for empty or invalid input."""
        return {
            "text": "",
            "cleaned_text": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "social_determinants": {},
            "living_conditions": {},
            "support_networks": {},
            "risk_factors": [],
            "protective_factors": [],
            "overall_assessment": {
                "overall_score": 0.0,
                "risk_level": "minimal",
                "support_level": "unknown",
                "living_conditions_level": "unknown",
                "social_determinants_level": "unknown",
                "domain_scores": {
                    "social_determinants": 0.0,
                    "living_conditions": 0.0,
                    "support_networks": 0.0
                },
                "summary": "Insufficient data for situational awareness assessment."
            },
            "analysis_details": {
                "text_length": 0,
                "word_count": 0,
                "sentence_count": 0,
                "character_count": 0,
                "language_detected": "unknown",
                "analysis_completeness": "failed"
            }
        }

# Global instance for reuse
_situational_awareness_service = None

def get_situational_awareness_service() -> SituationalAwarenessService:
    """Get or create the global Situational Awareness service instance."""
    global _situational_awareness_service
    if _situational_awareness_service is None:
        _situational_awareness_service = SituationalAwarenessService()
    return _situational_awareness_service

def analyze_situational_awareness(text: str) -> Dict[str, Any]:
    """
    Convenience function to analyze situational awareness from text.
    
    Args:
        text: Input text to analyze
        
    Returns:
        Dictionary containing situational awareness analysis
    """
    service = get_situational_awareness_service()
    return service.analyze_situational_awareness(text)