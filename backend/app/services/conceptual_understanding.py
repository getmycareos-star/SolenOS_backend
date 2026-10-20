"""
Conceptual Understanding Service for richer medical concept hierarchies and relationship modeling.
Builds upon the existing solenos care graph to provide enhanced conceptual reasoning.
"""
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Union, Set
from enum import Enum
import json

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)

class ConceptualUnderstandingService:
    """Service for enhancing medical concept understanding with hierarchies and relationships."""
    
    def __init__(self):
        """Initialize the Conceptual Understanding service."""
        # In a full implementation, we would load medical ontologies here
        # For now, we'll work with the existing care graph structure
        self._init_medical_mappings()
        logger.info("Conceptual Understanding service initialized")
    
    def _init_medical_mappings(self):
        """Initialize mappings to medical ontologies and create concept hierarchies."""
        # These would normally be loaded from external sources like SNOMED CT, ICD, etc.
        # For demonstration, we'll define some concept hierarchies
        
        # Condition hierarchies (simplified)
        self.condition_hierarchies = {
            "cardiovascular_disease": [
                "hypertension",
                "coronary_artery_disease", 
                "heart_failure",
                "stroke",
                "arrhythmia"
            ],
            "diabetes": [
                "type_1_diabetes",
                "type_2_diabetes",
                "gestational_diabetes"
            ],
            "respiratory_disease": [
                "asthma",
                "copd",
                "pneumonia",
                "bronchitis"
            ],
            "mental_health": [
                "depression",
                "anxiety_disorder",
                "bipolar_disorder",
                "schizophrenia"
            ]
        }
        
        # Medication hierarchies (simplified)
        self.medication_hierarchies = {
            "antihypertensive": [
                "ace_inhibitor",
                "beta_blocker",
                "calcium_channel_blocker",
                "diuretic"
            ],
            "antibiotic": [
                "penicillin",
                "cephalosporin",
                "macrolide",
                "fluoroquinolone"
            ],
            "analgesic": [
                "opioid",
                "nsaid",
                "acetaminophen"
            ]
        }
        
        # Create reverse mappings for quick lookup
        self.condition_to_parent = {}
        for parent, children in self.condition_hierarchies.items():
            for child in children:
                self.condition_to_parent[child] = parent
                
        self.medication_to_parent = {}
        for parent, children in self.medication_hierarchies.items():
            for child in children:
                self.medication_to_parent[child] = parent
    
    def enhance_concept_with_hierarchy(self, concept: str, concept_type: str = "condition") -> Dict[str, Any]:
        """
        Enhance a concept with hierarchical information (parents, children, siblings).
        
        Args:
            concept: The concept to enhance
            concept_type: Type of concept ("condition", "medication", etc.)
            
        Returns:
            Dictionary containing hierarchical information
        """
        result = {
            "concept": concept,
            "concept_type": concept_type,
            "parents": [],
            "children": [],
            "siblings": [],
            "level": 0,
            "path_to_root": [],
            "enhanced": False
        }
        
        try:
            if concept_type == "condition":
                hierarchies = self.condition_hierarchies
                to_parent = self.condition_to_parent
            elif concept_type == "medication":
                hierarchies = self.medication_hierarchies
                to_parent = self.medication_to_parent
            else:
                return result
            
            # Check if concept is a parent (has children)
            if concept in hierarchies:
                result["children"] = hierarchies[concept]
                result["enhanced"] = True
                result["level"] = 0  # Root level
                
            # Check if concept is a child (has parent)
            elif concept in to_parent:
                parent = to_parent[concept]
                result["parents"] = [parent]
                result["level"] = 1
                
                # Find siblings (other children of the same parent)
                if parent in hierarchies:
                    result["siblings"] = [child for child in hierarchies[parent] if child != concept]
                
                # Build path to root (simplified - assuming only one level deep for now)
                result["path_to_root"] = [concept, parent]
                result["enhanced"] = True
                
                # Check if parent is itself a child of another concept (for deeper hierarchies)
                if parent in to_parent:
                    grandparent = to_parent[parent]
                    result["parents"] = [parent, grandparent]
                    result["level"] = 2
                    result["path_to_root"] = [concept, parent, grandparent]
                    
                    # Update siblings to include parent's siblings' children
                    if grandparent in hierarchies:
                        parent_siblings = [child for child in hierarchies[grandparent] if child != parent]
                        for sibling_parent in parent_siblings:
                            if sibling_parent in hierarchies:
                                result["siblings"].extend(hierarchies[sibling_parent])
                                
            # If not found in our hierarchies, still return the basic structure
            # In a full implementation, we would query external ontologies
            
        except Exception as e:
            logger.warning(f"Error enhancing concept {concept} with hierarchy: {e}")
        
        return result
    
    def calculate_concept_similarity(self, concept1: str, concept2: str, 
                                   concept_type: str = "condition") -> float:
        """
        Calculate similarity between two concepts based on their hierarchical relationships.
        
        Args:
            concept1: First concept
            concept2: Second concept
            concept_type: Type of concepts
            
        Returns:
            Similarity score between 0 and 1
        """
        try:
            # Get hierarchical information for both concepts
            info1 = self.enhance_concept_with_hierarchy(concept1, concept_type)
            info2 = self.enhance_concept_with_hierarchy(concept2, concept_type)
            
            # If either concept wasn't enhanced, return low similarity
            if not info1["enhanced"] or not info2["enhanced"]:
                # Fall back to string similarity for exact matches or substrings
                if concept1.lower() == concept2.lower():
                    return 1.0
                elif concept1.lower() in concept2.lower() or concept2.lower() in concept1.lower():
                    return 0.5
                else:
                    return 0.0
            
            # Calculate similarity based on shared hierarchy
            # Jaccard similarity of ancestor sets
            ancestors1 = set(info1["path_to_root"])
            ancestors2 = set(info2["path_to_root"])
            
            # Add parents to ancestor sets
            ancestors1.update(info1["parents"])
            ancestors2.update(info2["parents"])
            
            intersection = len(ancestors1 & ancestors2)
            union = len(ancestors1 | ancestors2)
            
            if union == 0:
                return 0.0
                
            jaccard_similarity = intersection / union
            
            # Boost similarity for direct parent-child relationships
            if concept1 in info2["parents"] or concept2 in info1["parents"]:
                jaccard_similarity = max(jaccard_similarity, 0.8)
                
            # Boost for siblings
            if concept1 in info2["siblings"] or concept2 in info1["siblings"]:
                jaccard_similarity = max(jaccard_similarity, 0.7)
                
            return min(1.0, jaccard_similarity)
            
        except Exception as e:
            logger.warning(f"Error calculating similarity between {concept1} and {concept2}: {e}")
            return 0.0
    
    def infer_related_concepts(self, concept: str, concept_type: str = "condition", 
                              max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Infer related concepts based on hierarchical relationships.
        
        Args:
            concept: Base concept to find relations for
            concept_type: Type of concept
            max_results: Maximum number of related concepts to return
            
        Returns:
            List of related concepts with relationship types and scores
        """
        related = []
        
        try:
            # Get hierarchical information
            info = self.enhance_concept_with_hierarchy(concept, concept_type)
            
            # Add parents (generalization)
            for parent in info["parents"]:
                related.append({
                    "concept": parent,
                    "relationship": "parent_of",
                    "direction": "generalization",
                    "score": 0.9,
                    "description": f"{concept} is a type of {parent}"
                })
            
            # Add children (specialization)
            for child in info["children"]:
                related.append({
                    "concept": child,
                    "relationship": "child_of",
                    "direction": "specialization",
                    "score": 0.9,
                    "description": f"{child} is a type of {concept}"
                })
            
            # Add siblings (peers)
            for sibling in info["siblings"]:
                related.append({
                    "concept": sibling,
                    "relationship": "sibling_of",
                    "direction": "peer",
                    "score": 0.7,
                    "description": f"{concept} and {sibling} are both types of {info['parents'][0] if info['parents'] else 'a common parent'}"
                })
            
            # Sort by score and limit results
            related.sort(key=lambda x: x["score"], reverse=True)
            return related[:max_results]
            
        except Exception as e:
            logger.warning(f"Error inferring related concepts for {concept}: {e}")
            return []
    
    def map_to_external_ontology(self, concept: str, concept_type: str = "condition",
                                target_ontology: str = "snomed") -> Dict[str, Any]:
        """
        Map a concept to an external medical ontology (SNOMED, ICD, etc.).
        
        Args:
            concept: Concept to map
            concept_type: Type of concept
            target_ontology: Target ontology ("snomed", "icd10", "icd9", "loinc", "rxnorm")
            
        Returns:
            Mapping information including ontology codes and confidence
        """
        # In a full implementation, this would query actual ontology mappings
        # For now, we'll return a simulated mapping
        
        result = {
            "source_concept": concept,
            "source_type": concept_type,
            "target_ontology": target_ontology,
            "mappings": [],
            "confidence": 0.0,
            "mapping_method": "lookup"
        }
        
        try:
            # Simulate some known mappings for demonstration
            known_mappings = {
                ("hypertension", "condition", "snomed"): [
                    {"code": "38341003", "display": "Hypertensive disorder", "confidence": 0.95}
                ],
                ("type_2_diabetes", "condition", "snomed"): [
                    {"code": "44054006", "display": "Type 2 diabetes mellitus", "confidence": 0.98}
                ],
                ("lisinopril", "medication", "rxnorm"): [
                    {"code": "29046", "display": "Lisinopril", "confidence": 0.99}
                ],
                ("metformin", "medication", "rxnorm"): [
                    {"code": "8609", "display": "Metformin", "confidence": 0.99}
                ]
            }
            
            key = (concept.lower(), concept_type, target_ontology.lower())
            if key in known_mappings:
                result["mappings"] = known_mappings[key]
                if result["mappings"]:
                    result["confidence"] = max(mapping["confidence"] for mapping in result["mappings"])
            else:
                # Provide a fallback based on concept name matching
                # This would be much more sophisticated in a real implementation
                result["mappings"] = [{
                    "code": "UNKNOWN",
                    "display": f"Mapping for {concept} not found in {target_ontology}",
                    "confidence": 0.1
                }]
                result["confidence"] = 0.1
                
        except Exception as e:
            logger.warning(f"Error mapping concept {concept} to {target_ontology}: {e}")
            result["error"] = str(e)
        
        return result
    
    def enrich_care_graph_node(self, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich a care graph node with conceptual understanding information.
        
        Args:
            node_data: Dictionary containing node information
            
        Returns:
            Enhanced node data with conceptual understanding enhancements
        """
        try:
            enhanced_node = node_data.copy()
            
            # Extract concept information from the node
            label = node_data.get("label", "")
            canonical_entity_type = node_data.get("canonical_entity_type", "")
            
            # Determine concept type based on node type or canonical entity type
            concept_type = self._determine_concept_type(canonical_entity_type, node_data.get("node_type", ""))
            
            if label and concept_type:
                # Enhance the concept with hierarchical information
                hierarchy_info = self.enhance_concept_with_hierarchy(label, concept_type)
                enhanced_node["concept_hierarchy"] = hierarchy_info
                
                # Add mappings to external ontologies
                ontology_mappings = {}
                for ontology in ["snomed", "icd10", "rxnorm"]:
                    mapping = self.map_to_external_ontology(label, concept_type, ontology)
                    if mapping["confidence"] > 0.5:  # Only include high-confidence mappings
                        ontology_mappings[ontology] = mapping
                
                enhanced_node["ontology_mappings"] = ontology_mappings
                
                # Calculate conceptual richness score
                richness_factors = [
                    len(hierarchy_info.get("parents", [])),
                    len(hierarchy_info.get("children", [])),
                    len(hierarchy_info.get("siblings", [])),
                    len([m for m in ontology_mappings.values() if m.get("confidence", 0) > 0.7])
                ]
                
                # Normalize richness score (0-1)
                max_possible = 10  # Adjust based on what we consider "rich"
                richness_score = min(1.0, sum(richness_factors) / max_possible)
                enhanced_node["conceptual_richness_score"] = richness_score
                
                # Add related concepts
                related_concepts = self.infer_related_concepts(label, concept_type, max_results=3)
                enhanced_node["related_concepts"] = related_concepts
                
                # Add conceptual understanding metadata
                enhanced_node["conceptual_understanding"] = {
                    "enhanced_at": datetime.now(timezone.utc).isoformat(),
                    "enrichment_version": "1.0",
                    "sources": ["internal_hierarchy", "external_ontologies"] if ontology_mappings else ["internal_hierarchy"]
                }
            
            return enhanced_node
            
        except Exception as e:
            logger.warning(f"Error enriching care graph node: {e}")
            # Return original data if enhancement fails
            return node_data
    
    def _determine_concept_type(self, canonical_entity_type: str, node_type: str) -> str:
        """Determine the conceptual type for hierarchy enrichment."""
        # Map care graph types to our conceptual types
        concept_type_mapping = {
            "condition": "condition",
            "diagnosis": "condition",
            "medication": "medication",
            "symptom": "symptom",
            "procedure": "procedure",
            "allergy": "condition",  # Treat allergies as conditions for hierarchy
            "immunization": "medication",
            "device": "device",
            "observation": "observation",
            "result": "observation",
            "vital_sign": "observation",
            "measurement": "observation"
        }
        
        # Check canonical entity type first
        if canonical_entity_type in concept_type_mapping:
            return concept_type_mapping[canonical_entity_type]
        
        # Then check node type
        if node_type in concept_type_mapping:
            return concept_type_mapping[node_type]
        
        # Default to condition for medical concepts
        return "condition"
    
    def batch_enhance_concepts(self, concepts: List[Tuple[str, str]]) -> Dict[str, Any]:
        """
        Enhance multiple concepts in batch for efficiency.
        
        Args:
            concepts: List of (concept, concept_type) tuples
            
        Returns:
            Dictionary mapping concepts to their enhanced information
        """
        results = {}
        
        try:
            for concept, concept_type in concepts:
                if concept and concept_type:
                    results[concept] = self.enhance_concept_with_hierarchy(concept, concept_type)
            
            # Also calculate pairwise similarities for closely related concepts
            if len(concepts) > 1:
                similarity_pairs = []
                for i, (concept1, type1) in enumerate(concepts):
                    for j, (concept2, type2) in enumerate(concepts[i+1:], i+1):
                        if type1 == type2:  # Only compare same types
                            similarity = self.calculate_concept_similarity(concept1, concept2, type1)
                            if similarity > 0.3:  # Only include meaningful similarities
                                similarity_pairs.append({
                                    "concept1": concept1,
                                    "concept2": concept2,
                                    "similarity": similarity,
                                    "concept_type": type1
                                })
                
                # Sort by similarity and add to results
                similarity_pairs.sort(key=lambda x: x["similarity"], reverse=True)
                results["_similarity_pairs"] = similarity_pairs[:10]  # Top 10 pairs
            
        except Exception as e:
            logger.warning(f"Error in batch concept enhancement: {e}")
            results["error"] = str(e)
        
        return results

# Global instance for reuse
_conceptual_service = None

def get_conceptual_service() -> ConceptualUnderstandingService:
    """Get or create the global Conceptual Understanding service instance."""
    global _conceptual_service
    if _conceptual_service is None:
        _conceptual_service = ConceptualUnderstandingService()
    return _conceptual_service

def enhance_concept_with_hierarchy(concept: str, concept_type: str = "condition") -> Dict[str, Any]:
    """
    Convenience function to enhance a concept with hierarchical information.
    
    Args:
        concept: The concept to enhance
        concept_type: Type of concept ("condition", "medication", etc.)
        
    Returns:
        Dictionary containing hierarchical information
    """
    service = get_conceptual_service()
    return service.enhance_concept_with_hierarchy(concept, concept_type)

def calculate_concept_similarity(concept1: str, concept2: str, 
                                concept_type: str = "condition") -> float:
    """
    Convenience function to calculate similarity between two concepts.
    
    Args:
        concept1: First concept
        concept2: Second concept
        concept_type: Type of concepts
        
    Returns:
        Similarity score between 0 and 1
    """
    service = get_conceptual_service()
    return service.calculate_concept_similarity(concept1, concept2, concept_type)

def infer_related_concepts(concept: str, concept_type: str = "condition", 
                          max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Convenience function to infer related concepts.
    
    Args:
        concept: Base concept to find relations for
        concept_type: Type of concept
        max_results: Maximum number of related concepts to return
        
    Returns:
        List of related concepts with relationship types and scores
    """
    service = get_conceptual_service()
    return service.infer_related_concepts(concept, concept_type, max_results)

def map_to_external_ontology(concept: str, concept_type: str = "condition",
                            target_ontology: str = "snomed") -> Dict[str, Any]:
    """
    Convenience function to map a concept to an external medical ontology.
    
    Args:
        concept: Concept to map
        concept_type: Type of concept
        target_ontology: Target ontology ("snomed", "icd10", "icd9", "loinc", "rxnorm")
        
    Returns:
        Mapping information including ontology codes and confidence
    """
    service = get_conceptual_service()
    return service.map_to_external_ontology(concept, concept_type, target_ontology)

def enrich_care_graph_node(node_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function to enrich a care graph node with conceptual understanding.
    
    Args:
        node_data: Dictionary containing node information
        
    Returns:
        Enhanced node data with conceptual understanding enhancements
    """
    service = get_conceptual_service()
    return service.enrich_care_graph_node(node_data)