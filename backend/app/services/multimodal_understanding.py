"""
Multimodal Understanding Service for joint reasoning across text, images, and sensor data.
Integrates with the solenos document processing pipeline and clinical NER.
"""
import os
import io
import base64
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Union
from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

class MultimodalUnderstandingService:
    """Service for joint reasoning across text, images, and sensor data."""
    
    def __init__(self):
        """Initialize the Multimodal Understanding service."""
        # Try to initialize any required ML models
        self._init_models()
        logger.info("Multimodal Understanding service initialized")
    
    def _init_models(self):
        """Initialize any required machine learning models."""
        # Placeholder for model initialization
        # In a real implementation, you would load models here like:
        # - Vision models for image understanding (e.g., CLIP, medical vision models)
        # - Sensor data processing models
        # - Cross-modal fusion models
        self.vision_model = None
        self.sensor_model = None
        self.fusion_model = None
        
        # Try to initialize lightweight models if available
        try:
            # For now, we'll use rule-based and heuristic approaches
            # In production, you'd load actual models here
            logger.info("Multimodal models initialized (rule-based for now)")
        except Exception as e:
            logger.warning(f"Could not initialize some multimodal models: {e}")
    
    def process_image(self, image_data: Union[bytes, str, Image.Image]) -> Dict[str, Any]:
        """
        Process an image to extract medical-relevant information.
        
        Args:
            image_data: Image as bytes, base64 string, or PIL Image
            
        Returns:
            Dictionary containing extracted information from the image
        """
        try:
            # Convert input to PIL Image
            if isinstance(image_data, bytes):
                image = Image.open(io.BytesIO(image_data))
            elif isinstance(image_data, str):
                # Assume base64 encoded
                if image_data.startswith('data:image'):
                    # Remove data URL prefix
                    image_data = image_data.split(',')[1]
                image = Image.open(io.BytesIO(base64.b64decode(image_data)))
            elif isinstance(image_data, Image.Image):
                image = image_data
            else:
                raise ValueError("Unsupported image data type")
            
            # Extract basic image properties
            width, height = image.size
            mode = image.mode
            format = image.format
            
            # Medical image analysis (rule-based for now)
            medical_findings = self._analyze_medical_image(image)
            
            # OCR for text in images (like medication labels)
            ocr_text = self._extract_text_from_image(image)
            
            return {
                "image_properties": {
                    "width": width,
                    "height": height,
                    "mode": mode,
                    "format": format,
                    "size_bytes": len(image_data) if isinstance(image_data, bytes) else 0
                },
                "medical_findings": medical_findings,
                "extracted_text": ocr_text,
                "processing_timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            return {
                "error": str(e),
                "processing_timestamp": datetime.now().isoformat()
            }
    
    def _analyze_medical_image(self, image: Image.Image) -> Dict[str, Any]:
        """
        Analyze image for medical-relevant findings.
        This is a rule-based approach - in production would use trained models.
        """
        findings = {
            "wound_assessment": None,
            "medication_identification": None,
            "skin_condition": None,
            "general_observations": []
        }
        
        # Convert to RGB if needed
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Get image data for analysis
        img_array = np.array(image)
        avg_color = np.mean(img_array, axis=(0, 1))
        std_color = np.std(img_array, axis=(0, 1))
        
        # Simple heuristics for wound detection (reddish areas)
        r, g, b = avg_color
        if r > 100 and g < 80 and b < 80:  # Reddish
            findings["wound_assessment"] = {
                "possible_wound": True,
                "redness_score": float((r - max(g, b)) / 255),
                "confidence": 0.6  # Low confidence for heuristic
            }
        
        # Heuristics for pill/medication detection (round shapes, uniform color)
        # This would be much more sophisticated in a real implementation
        if std_color[0] < 30 and std_color[1] < 30 and std_color[2] < 30:
            findings["medication_identification"] = {
                "possible_medication": True,
                "uniformity_score": float(1.0 - np.mean(std_color) / 255),
                "confidence": 0.5
            }
        
        # Skin condition heuristics
        if 20 < r < 200 and 20 < g < 200 and 20 < b < 200:  # Skin-like tones
            findings["skin_condition"] = {
                "skin_tone_detected": True,
                "average_rgb": [float(r), float(g), float(b)],
                "confidence": 0.7
            }
        
        return findings
    
    def _extract_text_from_image(self, image: Image.Image) -> Dict[str, Any]:
        """
        Extract text from image using OCR (for medication labels, etc.).
        """
        try:
            import pytesseract
            
            # Configure tesseract for better medical text recognition
            custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,/- mg%'
            
            text = pytesseract.image_to_string(image, config=custom_config)
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config=custom_config)
            
            # Calculate average confidence
            confidences = [int(conf) for conf in data['conf'] if int(conf) > 0]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            return {
                "text": text.strip(),
                "confidence": avg_confidence / 100.0,  # Convert to 0-1 scale
                "word_count": len(text.split()) if text.strip() else 0,
                "has_text": bool(text.strip())
            }
        except ImportError:
            logger.warning("Pytesseract not available for OCR")
            return {
                "text": "",
                "confidence": 0.0,
                "word_count": 0,
                "has_text": False,
                "error": "OCR not available - pytesseract not installed"
            }
        except Exception as e:
            logger.error(f"OCR error: {e}")
            return {
                "text": "",
                "confidence": 0.0,
                "word_count": 0,
                "has_text": False,
                "error": str(e)
            }
    
    def process_sensor_data(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process sensor data to extract meaningful health information.
        
        Args:
            sensor_data: Dictionary containing sensor readings
            
        Returns:
            Dictionary containing processed sensor information
        """
        try:
            processed = {
                "vital_signs": {},
                "activity_metrics": {},
                "alerts": [],
                "processing_timestamp": datetime.now().isoformat()
            }
            
            # Process common vital signs
            if "heart_rate" in sensor_data:
                hr = sensor_data["heart_rate"]
                processed["vital_signs"]["heart_rate"] = {
                    "value": hr,
                    "unit": "bpm",
                    "status": self._assess_heart_rate(hr)
                }
            
            if "blood_pressure_systolic" in sensor_data and "blood_pressure_diastolic" in sensor_data:
                sys = sensor_data["blood_pressure_systolic"]
                dia = sensor_data["blood_pressure_diastolic"]
                processed["vital_signs"]["blood_pressure"] = {
                    "systolic": sys,
                    "diastolic": dia,
                    "unit": "mmHg",
                    "status": self._assess_blood_pressure(sys, dia)
                }
            
            if "temperature" in sensor_data:
                temp = sensor_data["temperature"]
                processed["vital_signs"]["temperature"] = {
                    "value": temp,
                    "unit": "°C",
                    "status": self._assess_temperature(temp)
                }
            
            if "oxygen_saturation" in sensor_data:
                spo2 = sensor_data["oxygen_saturation"]
                processed["vital_signs"]["oxygen_saturation"] = {
                    "value": spo2,
                    "unit": "%",
                    "status": self._assess_spo2(spo2)
                }
            
            # Process activity data
            if "steps" in sensor_data:
                processed["activity_metrics"]["steps_daily"] = sensor_data["steps"]
            
            if "heart_rate_variability" in sensor_data:
                hrv = sensor_data["heart_rate_variability"]
                processed["activity_metrics"]["hrv"] = {
                    "value": hrv,
                    "unit": "ms",
                    "status": self._assess_hrv(hrv)
                }
            
            # Generate alerts based on thresholds
            processed["alerts"] = self._generate_sensor_alerts(processed["vital_signs"])
            
            return processed
        except Exception as e:
            logger.error(f"Error processing sensor data: {e}")
            return {
                "error": str(e),
                "processing_timestamp": datetime.now().isoformat()
            }
    
    def _assess_heart_rate(self, hr: float) -> str:
        """Assess heart rate status."""
        if hr < 60:
            return "bradycardia"
        elif hr > 100:
            return "tachycardia"
        else:
            return "normal"
    
    def _assess_blood_pressure(self, sys: float, dia: float) -> str:
        """Assess blood pressure status."""
        if sys < 90 or dia < 60:
            return "hypotension"
        elif sys >= 140 or dia >= 90:
            return "hypertension"
        else:
            return "normal"
    
    def _assess_temperature(self, temp: float) -> str:
        """Assess temperature status."""
        if temp < 36.0:
            return "hypothermia"
        elif temp > 38.0:
            return "fever"
        else:
            return "normal"
    
    def _assess_spo2(self, spo2: float) -> str:
        """Assess oxygen saturation status."""
        if spo2 < 90:
            return "hypoxemia"
        elif spo2 < 95:
            return "low"
        else:
            return "normal"
    
    def _assess_hrv(self, hrv: float) -> str:
        """Assess heart rate variability status."""
        if hrv < 20:
            return "low_stress_resilience"
        elif hrv > 100:
            return "high_stress_resilience"
        else:
            return "normal"
    
    def _generate_sensor_alerts(self, vital_signs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate alerts based on vital sign assessments."""
        alerts = []
        
        for vital, data in vital_signs.items():
            if isinstance(data, dict) and "status" in data:
                status = data["status"]
                if status in ["bradycardia", "tachycardia", "hypotension", "hypertension", 
                             "hypothermia", "fever", "hypoxemia"]:
                    alerts.append({
                        "type": vital,
                        "status": status,
                        "message": f"Abnormal {vital}: {status}",
                        "severity": "high" if status in ["tachycardia", "hypertension", "fever"] else "medium",
                        "timestamp": datetime.now().isoformat()
                    })
        
        return alerts
    
    def fuse_multimodal_data(self, text_entities: List[Dict[str, Any]], 
                           image_results: Dict[str, Any],
                           sensor_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fuse information from text, images, and sensors for joint reasoning.
        
        Args:
            text_entities: Entities extracted from clinical text
            image_results: Results from image processing
            sensor_results: Results from sensor data processing
            
        Returns:
            Dictionary containing fused multimodal insights
        """
        try:
            fusion_result = {
                "text_entities": text_entities,
                "image_analysis": image_results,
                "sensor_analysis": sensor_results,
                "cross_modal_insights": [],
                "risk_assessment": {},
                "processing_timestamp": datetime.now().isoformat()
            }
            
            # Cross-modal reasoning: text + image
            text_image_insights = self._reason_text_image(text_entities, image_results)
            fusion_result["cross_modal_insights"].extend(text_image_insights)
            
            # Cross-modal reasoning: text + sensor
            text_sensor_insights = self._reason_text_sensor(text_entities, sensor_results)
            fusion_result["cross_modal_insights"].extend(text_sensor_insights)
            
            # Cross-modal reasoning: image + sensor
            image_sensor_insights = self._reason_image_sensor(image_results, sensor_results)
            fusion_result["cross_modal_insights"].extend(image_sensor_insights)
            
            # Generate risk assessment
            fusion_result["risk_assessment"] = self._generate_risk_assessment(
                text_entities, image_results, sensor_results
            )
            
            return fusion_result
        except Exception as e:
            logger.error(f"Error in multimodal fusion: {e}")
            return {
                "error": str(e),
                "processing_timestamp": datetime.now().isoformat()
            }
    
    def _reason_text_image(self, text_entities: List[Dict[str, Any]], 
                          image_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Reason about connections between text entities and image findings."""
        insights = []
        
        # Extract text entities by type
        text_dict = {}
        for entity in text_entities:
            label = entity.get('label', '').upper()
            if label not in text_dict:
                text_dict[label] = []
            text_dict[label].append(entity.get('text', '').lower())
        
        # Check for medication-text consistency
        if 'MEDICATION' in text_dict:
            med_texts = text_dict['MEDICATION']
            if image_results.get('extracted_text', {}).get('has_text'):
                img_text = image_results['extracted_text'].get('text', '').lower()
                # Check if any medication name appears in image text
                for med in med_texts:
                    if med in img_text:
                        insights.append({
                            "type": "medication_label_verification",
                            "description": f"Medication '{med}' mentioned in text matches label in image",
                            "confidence": 0.8,
                            "modalities": ["text", "image"]
                        })
        
        # Check for wound-related consistency
        wound_related = ['wound', 'ulcer', 'lesion', 'cut', 'abrasion']
        text_has_wound = any(
            any(word in entity.get('text', '').lower() for word in wound_related)
            for entity in text_entities
            if entity.get('label') in ['SYMPTOM', 'DISEASE']
        )
        
        image_suggests_wound = (
            image_results.get('medical_findings', {}).get('wound_assessment', {}).get('possible_wound', False)
        )
        
        if text_has_wound and image_suggests_wound:
            insights.append({
                "type": "wound_assessment_correlation",
                "description": "Text mentions wound-related symptoms and image shows possible wound",
                "confidence": 0.75,
                "modalities": ["text", "image"]
            })
        
        return insights
    
    def _reason_text_sensor(self, text_entities: List[Dict[str, Any]], 
                           sensor_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Reason about connections between text entities and sensor data."""
        insights = []
        
        vital_signs = sensor_results.get('vital_signs', {})
        
        # Check for hypertension in text vs sensor
        text_has_htn = any(
            'hypertension' in entity.get('text', '').lower()
            for entity in text_entities
            if entity.get('label') == 'DISEASE'
        )
        
        bp_status = vital_signs.get('blood_pressure', {}).get('status', '')
        if text_has_htn and bp_status == 'hypertension':
            insights.append({
                "type": "hypertension_confirmation",
                "description": "Text mentions hypertension and sensor data confirms high blood pressure",
                "confidence": 0.85,
                "modalities": ["text", "sensor"]
            })
        elif text_has_htn and bp_status == 'normal':
            insights.append({
                "type": "hypertension_discrepancy",
                "description": "Text mentions hypertension but sensor data shows normal blood pressure",
                "confidence": 0.7,
                "modalities": ["text", "sensor"]
            })
        
        # Check for diabetes in text vs sensor (would need glucose sensor)
        text_has_diabetes = any(
            'diabetes' in entity.get('text', '').lower()
            for entity in text_entities
            if entity.get('label') == 'DISEASE'
        )
        
        # If we had glucose sensor data, we could correlate here
        
        return insights
    
    def _reason_image_sensor(self, image_results: Dict[str, Any], 
                            sensor_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Reason about connections between image findings and sensor data."""
        insights = []
        
        # Example: If image shows edema and sensor shows weight gain
        # This would require more specific sensor data
        
        return insights
    
    def _generate_risk_assessment(self, text_entities: List[Dict[str, Any]], 
                                 image_results: Dict[str, Any],
                                 sensor_results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate overall risk assessment from multimodal data."""
        risk_factors = []
        protective_factors = []
        
        # From text entities
        for entity in text_entities:
            text = entity.get('text', '').lower()
            label = entity.get('label', '').upper()
            
            if label == 'DISEASE':
                if any(cond in text for cond in ['diabetes', 'hypertension', 'cancer', 'stroke']):
                    risk_factors.append({
                        "factor": text,
                        "source": "text",
                        "impact": "medium"
                    })
        
        # From sensor data
        vital_signs = sensor_results.get('vital_signs', {})
        for vital, data in vital_signs.items():
            if isinstance(data, dict):
                status = data.get('status', '')
                if status in ['hypertension', 'tachycardia', 'fever', 'hypoxemia']:
                    risk_factors.append({
                        "factor": f"Abnormal {vital}: {status}",
                        "source": "sensor",
                        "impact": "high"
                    })
                elif status in ['normal']:
                    protective_factors.append({
                        "factor": f"Normal {vital}",
                        "source": "sensor",
                        "impact": "low"
                    })
        
        # From image
        if image_results.get('medical_findings', {}).get('wound_assessment', {}).get('possible_wound'):
            risk_factors.append({
                "factor": "Possible wound detected",
                "source": "image",
                "impact": "medium"
            })
        
        # Calculate overall risk level
        high_risk_count = sum(1 for rf in risk_factors if rf.get('impact') == 'high')
        med_risk_count = sum(1 for rf in risk_factors if rf.get('impact') == 'medium')
        
        if high_risk_count > 0:
            overall_risk = "high"
        elif med_risk_count > 1:
            overall_risk = "medium"
        elif len(risk_factors) > 0:
            overall_risk = "low"
        else:
            overall_risk = "minimal"
        
        return {
            "risk_level": overall_risk,
            "risk_factors": risk_factors,
            "protective_factors": protective_factors,
            "confidence": 0.7  # Overall confidence in assessment
        }

# Global instance for reuse
_multimodal_service = None

def get_multimodal_service() -> MultimodalUnderstandingService:
    """Get or create the global Multimodal Understanding service instance."""
    global _multimodal_service
    if _multimodal_service is None:
        _multimodal_service = MultimodalUnderstandingService()
    return _multimodal_service

def process_multimodal_data(text_entities: List[Dict[str, Any]] = None,
                           image_data: Union[bytes, str, Image.Image] = None,
                           sensor_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Convenience function to process multimodal data and generate fused insights.
    
    Args:
        text_entities: List of entities from clinical text processing
        image_data: Image data to process
        sensor_data: Sensor data to process
        
    Returns:
        Dictionary containing fused multimodal understanding results
    """
    service = get_multimodal_service()
    
    # Process each modality if data is provided
    image_results = {}
    if image_data is not None:
        image_results = service.process_image(image_data)
    
    sensor_results = {}
    if sensor_data is not None:
        sensor_results = service.process_sensor_data(sensor_data)
    
    # Use empty list if no text entities provided
    if text_entities is None:
        text_entities = []
    
    # Fuse the multimodal data
    fusion_result = service.fuse_multimodal_data(text_entities, image_results, sensor_results)
    
    return fusion_result