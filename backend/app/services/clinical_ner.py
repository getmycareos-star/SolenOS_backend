"""
Clinical NER Service for extracting medical entities from clinical text.
Integrates with the solenos document processing pipeline.
"""
import os
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import spacy

logger = logging.getLogger(__name__)

class ClinicalNERService:
    """Service for extracting clinical entities from text using rule-based NER."""
    
    def __init__(self, model_name="en_core_web_sm"):
        """Initialize the Clinical NER service."""
        try:
            self.nlp = spacy.load(model_name)
            logger.info(f"Loaded spaCy model: {model_name}")
        except OSError:
            logger.error(f"Model {model_name} not found. Please install it using:")
            logger.error(f"python -m spacy download {model_name}")
            raise
        
        # Add clinical entity patterns to the pipeline
        self._add_clinical_patterns()
        logger.info("Clinical NER service initialized")
    
    def _add_clinical_patterns(self):
        """Add clinical entity patterns to the spaCy pipeline."""
        # Get the ruler component
        if "entity_ruler" not in self.nlp.pipe_names:
            ruler = self.nlp.add_pipe("entity_ruler", before="ner")
        else:
            ruler = self.nlp.get_pipe("entity_ruler")
        
        # Define clinical patterns
        patterns = [
            # Diseases and conditions
            {"label": "DISEASE", "pattern": [{"LOWER": "diabetes"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "hypertension"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "hyperlipidemia"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "myocardial"}, {"LOWER": "infarction"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "acute"}, {"LOWER": "coronary"}, {"LOWER": "syndrome"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "dementia"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "alzheimer"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "cancer"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "stroke"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "arthritis"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "asthma"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "copd"}]},
            {"label": "DISEASE", "pattern": [{"LOWER": "ckd"}]},  # Chronic kidney disease
            
            # Medications
            {"label": "MEDICATION", "pattern": [{"LOWER": "lisinopril"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "metformin"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "atorvastatin"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "aspirin"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "nitroglycerin"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "warfarin"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "insulin"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "amlodipine"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "metoprolol"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "losartan"}]},
            {"label": "MEDICATION", "pattern": [{"LOWER": "furosemide"}]},
            
            # Vital signs and measurements
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "mmhg"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "bpm"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "mm"}, {"LOWER": "hg"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "mg"}, {"LOWER": "dl"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "mmol"}, {"LOWER": "l"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "kg"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "cm"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "in"}]},
            {"label": "VITAL_SIGN", "pattern": [{"LIKE_NUM": True}, {"LOWER": "f"}]},
            
            # Dates and times
            {"label": "DATE", "pattern": [{"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "january"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "february"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "march"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "april"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "may"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "june"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "july"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "august"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "september"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "october"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "november"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "DATE", "pattern": [{"LOWER": "december"}, {"LOWER": "/"}, {"LIKE_NUM": True}, {"LOWER": "/"}, {"LIKE_NUM": True}]},
            {"label": "TIME", "pattern": [{"LIKE_NUM": True}, {"LOWER": ":"}, {"LIKE_NUM": True}]},
            {"label": "TIME", "pattern": [{"LIKE_NUM": True}, {"LOWER": "am"}]},
            {"label": "TIME", "pattern": [{"LIKE_NUM": True}, {"LOWER": "pm"}]},
            
            # Anatomy and body parts
            {"label": "ANATOMY", "pattern": [{"LOWER": "chest"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "arm"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "heart"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "lung"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "brain"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "kidney"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "liver"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "stomach"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "intestine"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "bone"}]},
            {"label": "ANATOMY", "pattern": [{"LOWER": "joint"}]},
            
            # Clinical procedures and tests
            {"label": "PROCEDURE", "pattern": [{"LOWER": "ekg"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "ecg"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "eeg"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "mri"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "ct"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "xray"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "ultrasound"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "biopsy"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "colonoscopy"}]},
            {"label": "PROCEDURE", "pattern": [{"LOWER": "endoscopy"}]},
            
            # Symptoms
            {"label": "SYMPTOM", "pattern": [{"LOWER": "pain"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "fever"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "nausea"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "vomiting"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "dizziness"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "fatigue"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "weakness"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "shortness of breath"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "cough"}]},
            {"label": "SYMPTOM", "pattern": [{"LOWER": "rash"}]},
        ]
        
        # Add patterns to the ruler
        ruler.add_patterns(patterns)
        logger.info(f"Added {len(patterns)} clinical patterns to the entity ruler.")
    
    def extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """
        Extract clinical entities from text.
        
        Args:
            text: Input text to process
            
        Returns:
            List of dictionaries containing entity information
        """
        if not text or not text.strip():
            return []
        
        # Preprocess text
        processed_text = self._preprocess_text(text)
        
        # Extract entities using spaCy
        doc = self.nlp(processed_text)
        entities = []
        
        for ent in doc.ents:
            entities.append({
                "text": ent.text,
                "label": ent.label_,
                "start": ent.start_char,
                "end": ent.end_char,
                "confidence": 0.85  # Default confidence for rule-based entities
            })
        
        logger.debug(f"Extracted {len(entities)} entities from text")
        return entities
    
    def extract_entities_with_groups(self, text: str) -> Dict[str, Any]:
        """
        Extract clinical entities and group them by entity type.
        
        Args:
            text: Input text to process
            
        Returns:
            Dictionary containing entities and grouped entities
        """
        entities = self.extract_entities(text)
        
        # Group entities by type
        grouped_entities = {}
        for ent in entities:
            label = ent['label']
            if label not in grouped_entities:
                grouped_entities[label] = []
            grouped_entities[label].append(ent)
        
        return {
            "raw_text": text,
            "processed_text": self._preprocess_text(text),
            "entities": entities,
            "grouped_entities": grouped_entities,
            "timestamp": datetime.now().isoformat(),
            "entity_count": len(entities)
        }
    
    def _preprocess_text(self, text: str) -> str:
        """Preprocess text by normalizing whitespace and fixing common issues."""
        # Replace multiple whitespaces with single space
        text = re.sub(r'\s+', ' ', text)
        # Replace newlines with spaces
        text = text.replace('\n', ' ')
        return text.strip()

# Global instance for reuse
_clinical_ner_service = None

def get_clinical_ner_service() -> ClinicalNERService:
    """Get or create the global Clinical NER service instance."""
    global _clinical_ner_service
    if _clinical_ner_service is None:
        _clinical_ner_service = ClinicalNERService()
    return _clinical_ner_service

def extract_clinical_entities(text: str) -> List[Dict[str, Any]]:
    """Convenience function to extract clinical entities from text."""
    service = get_clinical_ner_service()
    return service.extract_entities(text)

def extract_clinical_entities_with_groups(text: str) -> Dict[str, Any]:
    """Convenience function to extract clinical entities with grouping."""
    service = get_clinical_ner_service()
    return service.extract_entities_with_groups(text)
